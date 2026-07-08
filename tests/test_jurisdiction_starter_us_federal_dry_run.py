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
from types import SimpleNamespace

import pytest

from lawvm.core.ir import (
    IRNode,
    LegalAddress,
    LegalOperation,
    OperationSource,
    ScopePredicate,
    TextPatchSpec,
    TextSelector,
)
from lawvm.core.semantic_types import IRNodeKind, StructuralAction, TextPatchKindEnum
from lawvm.us_federal.amendatory import (
    HEADING_BODY_DUPLICATE_ANCHOR_OCCURRENCE_PROVENANCE,
    RULE_STRIKE_INSERT_THROUGH_TAIL,
    SENTENCE_ANCHOR_INSERT_FINDING_RULE_ID,
    SENTENCE_STRIKE_FINDING_RULE_ID,
    UNDESIGNATED_PARAGRAPH_OCCURRENCE_PROVENANCE,
    US_EACH_PLACE_STRIKE_EXCEPTION_DIMENSION,
    lower_plaw_amendatory,
)
from lawvm.us_federal.dry_run import (
    DISPOSITION_LAWVM_WRONG,
    DISPOSITION_MISSING_SOURCE,
    DISPOSITION_ORACLE_SUSPECT,
    US_DRY_RUN_NOT_REPLAY_AUTHORIZED_RULE_ID,
    US_DRY_RUN_REFUSED_SECTION_NOT_IN_BEFORE_RULE_ID,
    US_DRY_RUN_REFUSED_STRUCTURAL_NOT_SECTION_REPRESENTABLE_RULE_ID,
    US_DRY_RUN_REFUSED_TARGET_NOT_TITLE_RULE_ID,
    US_DRY_RUN_REFUSED_TEXT_TARGET_NODE_ABSENT_RULE_ID,
    US_DRY_RUN_REFUSED_DEFERRED_OP_NOT_YET_EFFECTIVE_RULE_ID,
    US_DRY_RUN_DEFERRED_OP_INFLATED_AS_MISSING_SOURCE_RULE_ID,
    US_DRY_RUN_RESIDUAL_ORACLE_CHANGED_NOT_CLAIMED_RULE_ID,
    US_DRY_RUN_RESIDUAL_OLRC_GRAMMAR_CLEANUP_RULE_ID,
    US_DRY_RUN_RESIDUAL_ORACLE_RETAINED_TITLE_SCOPE_STRIKE_RULE_ID,
    US_DRY_RUN_RESIDUAL_SOURCE_TRUNCATED_PAYLOAD_RULE_ID,
    US_DRY_RUN_RESIDUAL_SUBSECTION_NODE_NOT_LOCATED_RULE_ID,
    US_DRY_RUN_RESIDUAL_PARTIAL_COMPOSITION_MID_CHAIN_GAP_RULE_ID,
    US_DRY_RUN_RESIDUAL_SOURCE_TREE_PARSE_AMBIGUOUS_RULE_ID,
    US_DRY_RUN_RESIDUAL_TARGET_ANCESTOR_ABSENT_IN_SOURCE_TREE_RULE_ID,
    US_DRY_RUN_RESIDUAL_TARGET_LEVEL_ABSENT_IN_SOURCE_TREE_RULE_ID,
    US_DRY_RUN_RESIDUAL_TEXT_MISMATCH_RULE_ID,
    US_DRY_RUN_SECTION_AGREES_RULE_ID,
    US_DRY_RUN_RECOVERED_BARE_LEAF_TARGET_VIA_UNIQUE_SUFFIX_RULE_ID,
    USDryRunConservedAccount,
    USDryRunRefusal,
    USDryRunReport,
    USDryRunTargetRecovery,
    USDryRunWindowError,
    _has_source_truncated_clause_payload,
    _index_node_text,
    _locate_subsection_text,
    _locate_subsection_text_resolved,
    _materialize_one,
    _norm_editorial,
    _replace_token_tail_in_text,
    _running_node_text,
    _subsection_segments,
    build_us_dry_run,
    build_us_dry_run_conserved_account,
)
from lawvm.us_federal.us_ordering import US_SAME_MOMENT_CONFLICT_KIND
from lawvm.us_federal.source_tree import UscSection, parse_usc_title_document, synthetic_usc_section, split_statutory_subsections

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


def test_us_dry_run_conserved_account_projects_section_surface_accounting():
    report = _build()

    account = build_us_dry_run_conserved_account(report)

    assert isinstance(account, USDryRunConservedAccount)
    assert account.section_rows == report.rows
    assert account.refused_ops == report.refusals
    assert account.row_count == len(report.rows)
    assert account.refused_count == len(report.refusals)
    assert account.has_accounting_surface
    assert account.replay_authorized is False

    families = {
        residual["family"]
        for residual in account.agreement_surface_residuals
        if isinstance(residual.get("family"), str)
    }
    rule_ids = {
        residual["rule_id"]
        for residual in account.agreement_surface_residuals
        if isinstance(residual.get("rule_id"), str)
    }
    assert "agreement" in families
    assert "source_footing_gap" in families
    assert US_DRY_RUN_RESIDUAL_ORACLE_CHANGED_NOT_CLAIMED_RULE_ID in rule_ids


# ---------------------------------------------------------------------------
# Synthetic window: agreement + boundary + north-star
# ---------------------------------------------------------------------------


def test_replace_token_in_text_last_occurrence_replaces_rightmost_match_once():
    """TextSelector.occurrence_mode='Last' is honored by the regular replace path.

    Without ``last_occurrence=True``, the legacy ``count=-1`` ALL semantics
    multiplies the op across every period in the section: a single
    'inserting 'X' before the period at the end' opswith three sentences gets
    X prepended before EACH period, creating a triple-insert materialization
    that silently multiplies the op's effect across the section. The typed
    'Last' carrier restricts the patch to the W-rightmost match ONCE.
    Concrete witness: PL 114-113 s709(a) on 12 U.S.C. 5230(b)
    ('inserting after the period at the end the following: 'Notwithstanding...'').

    AGENTS.md §0: no silent mutation beyond the target region. The op's target
    is ONE terminal period, not every period in the section.
    """
    from lawvm.us_federal.dry_run import _replace_token_in_text

    before_text = "First sentence. Second sentence. Third sentence."
    patched = _replace_token_in_text(
        before_text, match_text=".", replacement="X", count=-1, last_occurrence=True
    )
    assert patched == "First sentence. Second sentence. Third sentenceX"
    # The default 'Auto' mode preserves the legacy ALL-across-periods behavior
    # (each-place AL ops deliberately replace every occurrence).
    auto_patched = _replace_token_in_text(
        before_text, match_text=".", replacement="X", count=-1
    )
    assert auto_patched == "First sentenceX Second sentenceX Third sentenceX"
    # Single-occurrence text behaves identically across both modes.
    single = "Only one sentence here."
    assert _replace_token_in_text(
        single, match_text=".", replacement="X", count=-1, last_occurrence=True
    ) == _replace_token_in_text(single, match_text=".", replacement="X", count=-1)


def test_terminal_period_strike_inserts_olrc_courtesy_space_before_word_clause():
    """F3 §50:3919: "strike the period at the end and insert 'or as a member of the
    Space Force.'" removes the sentence-terminal period that directly abutted the
    preceding word (``reserve component.``). Mechanically concatenating the inserted
    continuation yields ``reserve componentor``; the OLRC renders ``reserve component
    or …`` with a single separating space. A bare-``.`` last-occurrence strike whose
    removed period joined a word to a WORD-INITIAL MULTI-WORD continuation clause gets
    the courtesy space so the materialization matches the enacted continuation.
    """
    from lawvm.us_federal.dry_run import _replace_token_in_text

    before = "or a reserve component. (6) A change."
    patched = _replace_token_in_text(
        before,
        match_text=".",
        replacement="or as a member of the Space Force.",
        count=-1,
        last_occurrence=True,
    )
    # The struck terminal period of the LAST sentence joined ``change`` to the
    # continuation; the earlier ``component.`` period is untouched (Last-occurrence).
    assert patched == "or a reserve component. (6) A change or as a member of the Space Force."

    # NARROWNESS 1: a single-token replacement (no internal space) is NOT a clause —
    # no courtesy space (keeps the punctuation/list-conjunction shapes byte-stable).
    assert _replace_token_in_text(
        "First. Second.", match_text=".", replacement="X", count=-1, last_occurrence=True
    ) == "First. SecondX"
    # NARROWNESS 2: a punctuation-led insert (``; and``) is a list conjunction, never a
    # word continuation — no courtesy space (the leading char is not alnum).
    assert _replace_token_in_text(
        "law. taxes", match_text=".", replacement="; and more", count=-1, last_occurrence=True
    ) == "law; and more taxes"


def test_quoted_strike_at_end_deletes_terminal_word_inside_target_node() -> None:
    # PL 114-22 §605 / 28:566 witness shape: "by striking 'and' at the end" of
    # subparagraph (B). The end-position selector deletes the list-conjunction
    # tail, not the first body occurrence in "within and outside".
    section = synthetic_usc_section(
        title=28,
        section="566",
        text=(
            "(e)(1) The Service may— "
            "(A) protect officials; "
            "(B) investigate such fugitive matters, both within and outside the "
            "United States, as directed by the Attorney General; and "
            "(C) issue subpoenas."
        ),
    )
    op = LegalOperation(
        op_id="strike-terminal-and",
        sequence=1,
        action=StructuralAction.TEXT_PATCH,
        target=LegalAddress(
            path=(
                ("title", "28"),
                ("section", "566"),
                ("subsection", "e"),
                ("paragraph", "1"),
                ("subparagraph", "B"),
            )
        ),
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.DELETE,
            selector=TextSelector(
                match_text="and",
                occurrence=-1,
                occurrence_mode="Last",
            ),
        ),
    )

    outcome = _materialize_one(op, section.statutory_text, before_section=section)

    assert not isinstance(outcome, USDryRunRefusal)
    materialized, signal_rule_id, disposition = outcome
    assert signal_rule_id == ""
    assert disposition == ""
    assert "within and outside the United States" in materialized
    assert "Attorney General; and (C)" not in materialized
    assert "Attorney General;  (C)" in materialized


def test_undesignated_paragraph_scope_patches_paragraph_not_global_match() -> None:
    # 35:112 AIA witness shape: repeated anchors in several undesignated
    # paragraphs. The parent source scope is a paragraph ordinal; applying it as
    # an nth global text occurrence would put headings in the wrong paragraph
    # once earlier sibling edits mutate the same repeated phrase.
    before_htm = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><body><div>'
        "<!-- expcite:TITLE 35-SYNTHETIC!@!CHAPTER 1-PROVISIONS!@!Sec. 112 -->"
        "<!-- field-start:head -->"
        '<h3 class="section-head">&sect;112. Specification</h3>'
        "<!-- field-end:head --><!-- field-start:statute -->"
        '<p class="statutory-body">The specification shall contain a written '
        "description of the invention.</p>"
        '<p class="statutory-body">The specification shall conclude with one '
        "or more claims.</p>"
        '<p class="statutory-body">A claim may be written in independent form.</p>'
        '<p class="statutory-body">Subject matter may be claimed.</p>'
        '<p class="statutory-body">A claim in dependent form shall contain a '
        "reference.</p>"
        '<p class="statutory-body">An element in a claim may be expressed as a '
        "means.</p>"
        "<!-- field-end:statute -->"
        "</div></body></html>"
    ).encode("utf-8")
    section = (
        parse_usc_title_document(before_htm, title=35, year="2010")
        .section_by_number("112")
    )
    assert section is not None
    running = section.statutory_text
    node_overrides: dict[tuple[tuple[str, str], ...], str] = {}
    target = LegalAddress(path=(("title", "35"), ("section", "112")))

    def patch_op(
        op_id: str,
        *,
        paragraph_index: int,
        old: str,
        new: str,
    ) -> LegalOperation:
        return LegalOperation(
            op_id=op_id,
            sequence=1,
            action=StructuralAction.TEXT_PATCH,
            target=target,
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text=old, occurrence=paragraph_index),
                replacement=new,
            ),
            provenance_tags=(
                "us_amendatory",
                UNDESIGNATED_PARAGRAPH_OCCURRENCE_PROVENANCE,
            ),
        )

    ops = [
        patch_op(
            "p0",
            paragraph_index=0,
            old="The specification",
            new="(a) In General.—The specification",
        ),
        patch_op(
            "p1",
            paragraph_index=1,
            old="The specification",
            new="(b) Conclusion.—The specification",
        ),
        patch_op(
            "p2",
            paragraph_index=2,
            old="A claim",
            new="(c) Form.—A claim",
        ),
        patch_op(
            "p4",
            paragraph_index=4,
            old="A claim",
            new="(e) Reference in Dependent Forms.—A claim",
        ),
    ]

    for op in ops:
        outcome = _materialize_one(
            op,
            running,
            before_section=section,
            node_overrides=node_overrides,
        )
        assert not isinstance(outcome, USDryRunRefusal)
        running, rule_id, disposition = outcome
        assert rule_id == ""
        assert disposition == ""

    assert running == (
        "(a) In General.—The specification shall contain a written description "
        "of the invention. (b) Conclusion.—The specification shall conclude "
        "with one or more claims. (c) Form.—A claim may be written in "
        "independent form. Subject matter may be claimed. "
        "(e) Reference in Dependent Forms.—A claim in dependent form shall "
        "contain a reference. An element in a claim may be expressed as a means."
    )


def test_s0_length_ratio_invariant_defends_against_silent_state_corruption() -> None:
    """§0 guard-liveness test: through-tail must preserve the right-side
    suffix AND end-punct-insert must target the W-rightmost period only.

    AGENTS.md §2.9 guard-liveness: drive known-violating inputs through the
    FULL production path (``build_us_dry_run``), not just a unit test of the
    helper function. This test catches two §0 silent-state-corruption bug
    classes discovered during the 2026-06 session:

      1. ``_replace_token_through_in_text`` dropped ``text[end_pos:]``
         (the bounded-deletion helper returned
         ``text[:start_pos] + replacement`` without the suffix) — every
         through-tail op silently converted to an open-ended tail cut.
         Regression signature: ``len(materialized) << len(oracle)`` on a
         section whose right-side text should survive.

      2. ``TextSelector.occurrence == -1`` was overloaded between EACH_PLACE
         (replace ALL occurrences) and LAST (replace rightmost once);
         ``str.replace(count=-1)`` means ALL in Python — multi-sentence
         sections with >1 terminal period got the insert applied to every
         period, silently multiplying the op's effect.
         Regression signature: ``len(materialized) >> len(oracle)`` on a
         multi-sentence section where one terminal-punct edit ran.

    The test drives both op families through ``build_us_dry_run`` end-to-end
    and asserts per-section length ratios against a band that the regressions
    would violate.
    """
    section_50_before = "Sentence one. Sentence two. Sentence three."
    section_50_after = "Sentence one. Sentence two. Sentence three; and."
    section_70_before = (
        "Preamble. Definitions. End block. Surplus text that is long enough"
        " to test truncation behavior at the end of the section."
    )
    section_70_after = (
        "Preamble. Budget Activity Defined. Surplus text that is long enough"
        " to test truncation behavior at the end of the section."
    )

    def _htm(s50: str, s70: str) -> bytes:
        return (
            '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN"\n'
            '"http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">\n'
            '<html xmlns="http://www.w3.org/1999/xhtml">\n'
            " <head>\n  <title>U.S.C. Title 99 (s0 guard)</title>\n"
            "<!-- AUTHORITIES-USC-TITLE-ENUM:99 -->\n"
            " </head>\n <body>\n  <div>\n"
            "<!-- expcite:TITLE 99-SYNTHETIC!@!CHAPTER 1-PROVISIONS!@!Sec. 50 -->\n"
            "<!-- field-start:head -->\n"
            '<h3 class="section-head">&sect;50. Multi-sentence</h3>\n'
            "<!-- field-end:head -->\n"
            "<!-- field-start:statute -->\n"
            f'<p class="statutory-body">{s50}</p>\n'
            "<!-- field-end:statute -->\n"
            "<!-- field-start:sourcecredit -->\n"
            '<p class="source-credit">(Pub. L. 99&ndash;1, Jan. 1, 2020, 100 Stat. 1.)</p>\n'
            "<!-- field-end:sourcecredit -->\n"
            "<!-- expcite:TITLE 99-SYNTHETIC!@!CHAPTER 1-PROVISIONS!@!Sec. 70 -->\n"
            "<!-- field-start:head -->\n"
            '<h3 class="section-head">&sect;70. Multi-block</h3>\n'
            "<!-- field-end:head -->\n"
            "<!-- field-start:statute -->\n"
            f'<p class="statutory-body">{s70}</p>\n'
            "<!-- field-end:statute -->\n"
            "<!-- field-start:sourcecredit -->\n"
            '<p class="source-credit">(Pub. L. 99&ndash;1, Jan. 1, 2020, 100 Stat. 1.)</p>\n'
            "<!-- field-end:sourcecredit -->\n"
            "  </div>\n </body>\n</html>\n"
        ).encode("utf-8")

    plaw = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<uslm xmlns="http://schemas.gpo.gov/xml/uslm"><meta>'
        "<congress>99</congress><docNumber>3</docNumber>"
        "<approvedDate>2024-01-01</approvedDate></meta><main>"
        "<section><num>1</num><content>"
        '<ref href="/us/usc/t99/s50">Section 50 of title 99, United States Code</ref>, '
        '<amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="insert">inserting</amendingAction> '
        "\u201c<quotedText>; and</quotedText>\u201d before the period at the end."
        "</content></section>"
        "<section><num>2</num><content>"
        '<ref href="/us/usc/t99/s70">Section 70 of title 99, United States Code</ref>, '
        '<amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="delete">striking</amendingAction> '
        "\u201c<quotedText>Definitions</quotedText>\u201d and all that follows through "
        "\u201c<quotedText>End block.</quotedText>\u201d and "
        '<amendingAction type="insert">inserting</amendingAction> '
        "\u201c<quotedText>Budget Activity Defined.</quotedText>\u201d."
        "</content></section>"
        "</main></uslm>"
    ).encode("utf-8")

    report = build_us_dry_run(
        before_htm=_htm(section_50_before, section_70_before),
        after_htm=_htm(section_50_after, section_70_after),
        plaw_blobs={"PL 99-3": plaw},
        title=99,
        before_year="2023",
        after_year="2024",
    )
    rows = {r.section_key: r for r in report.rows}
    assert "99:50" in rows, list(rows)
    assert "99:70" in rows, list(rows)

    # Section 50 (insert_end_punct on a multi-sentence section).
    # If OccMode bug regressed (every period got "; and" prepended), the
    # materialized text would be ~30% longer than the oracle.
    s50 = rows["99:50"]
    oracle_50 = max(len(s50.oracle_text), 1)
    ratio_50 = len(s50.materialized_text) / oracle_50
    assert 0.9 <= ratio_50 <= 1.10, (
        f"section 50 ratio {ratio_50:.2f} out of [0.9, 1.10] band; "
        f"mat_len={len(s50.materialized_text)} orc_len={oracle_50}; "
        f"mat={s50.materialized_text!r}"
    )

    # Section 70 (through-tail strike-insert whose right-side text must survive).
    # If through-tail bug regressed (text[end_pos:] dropped), the materialized
    # text would be ~60% shorter than the oracle.
    s70 = rows["99:70"]
    oracle_70 = max(len(s70.oracle_text), 1)
    ratio_70 = len(s70.materialized_text) / oracle_70
    assert 0.50 <= ratio_70 <= 1.50, (
        f"section 70 ratio {ratio_70:.2f} out of [0.50, 1.50] band; "
        f"mat_len={len(s70.materialized_text)} orc_len={oracle_70}; "
        f"mat={s70.materialized_text!r}"
    )


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
    assert agree.row_status == "agree"
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
    assert proof.boundary_proof_status == "unresolved"
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


def test_deferred_op_on_oracle_changed_section_reclassifies_from_missing_source() -> None:
    # §0 guard-liveness: when LawVM lowers the right amendment but its statutory
    # effective date is after the after-edition cutoff, the op is deferred. If the
    # oracle's after-edition text already reflects that deferred amendment (OLRC
    # editorial pre-dating), the section must be reclassified from
    # missing_source → oracle_suspect, NOT left as a false-positive lowering gap.
    #
    # Oracle: section 10 changed between before/after editions (15-year → 19-year).
    # PLAW: amends section 10 but with "effective 1 year after enactment" (2025-01-01
    # > 2024-12-31 cutoff). The op is correctly deferred; section 10 is
    # oracle-changed-but-not-claimed → must be deferred_op, not missing_source.
    plaw_future = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<uslm xmlns="http://schemas.gpo.gov/xml/uslm"><meta>'
        b'<congress>99</congress><docNumber>5</docNumber>'
        b'<approvedDate>2024-01-01</approvedDate></meta><main>'
        b'<section identifier="/us/pl/99/5/s1"><num value="1">SEC. 1. </num>'
        b'<content>Effective on the date that is 1 year after the date of '
        b'enactment of this Act, '
        b'<ref href="/us/usc/t99/s10">Section 10 of title 99, United '
        b'States Code</ref>, <amendingAction type="amend">is amended</amendingAction> '
        b'by <amendingAction type="delete">striking</amendingAction> '
        b'\xe2\x80\x9c<quotedText>15-year</quotedText>\xe2\x80\x9d and '
        b'<amendingAction type="insert">inserting</amendingAction> '
        b'\xe2\x80\x9c<quotedText>19-year</quotedText>\xe2\x80\x9d.'
        b'</content></section>'
        b'</main></uslm>'
    )
    report = _build(plaw_blobs={"PL 99-5": plaw_future})

    # The op was correctly deferred (future effective date).
    deferred = [
        r for r in report.refusals
        if r.rule_id == US_DRY_RUN_REFUSED_DEFERRED_OP_NOT_YET_EFFECTIVE_RULE_ID
    ]
    assert deferred, "expected a deferred-op refusal"

    # Section 10 IS oracle-changed but NOT claimed (the only op targeting it was
    # deferred, so no materialization was attempted).
    ns = report.north_star()
    assert "99:10" in report.oracle_changed_sections
    assert "99:10" not in report.claimed_sections

    # Section 10 must be reclassified as deferred_op (oracle_suspect), NOT
    # missing_source (which would be a false-positive lowering gap).
    assert "99:10" not in ns["missing_source_sections"]
    assert "99:10" in ns.get("deferred_op_sections", [])

    # The agreement surface carries the deferred-inflated residual, not the
    # missing_source one.
    surface = report.agreement_surface()
    rule_ids = {r["rule_id"] for r in surface["residuals"]}
    assert US_DRY_RUN_DEFERRED_OP_INFLATED_AS_MISSING_SOURCE_RULE_ID in rule_ids
    # The missing_source residual for section 10 is NOT emitted.
    assert "99:10" not in {
        r["detail"].get("section_key", "")
        for r in surface["residuals"]
        if r["rule_id"] == US_DRY_RUN_RESIDUAL_ORACLE_CHANGED_NOT_CLAIMED_RULE_ID
    }


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


def test_strike_anchor_absent_from_the_section_is_refused_never_fuzzy_matched() -> None:
    # Strike a phrase that does not occur in section 10's before text: the target
    # node this op edits is absent from the window's before edition. We never
    # fuzzy-match; the op is REFUSED (mirroring REPEAL), not materialized. With this
    # the sole op as the only claim on the section, the section is not claimed at all
    # (a refusal makes no materialization claim) and never a wrong materialization.
    pl = _plaw_bytes_with_target_and_strike(99, "10", "nonexistent-phrase")
    report = _build({"PL 99-3": pl})
    rows = {row.section_key: row for row in report.rows}
    # No materialized row is published for a section whose only op was refused.
    assert "99:10" not in rows
    # The op surfaces as a visible typed refusal carrying the offending anchor.
    refusals = [r for r in report.refusals if r.rule_id == US_DRY_RUN_REFUSED_TEXT_TARGET_NODE_ABSENT_RULE_ID]
    assert len(refusals) == 1
    assert "nonexistent-phrase" in refusals[0].message


def test_wrong_replacement_is_a_text_mismatch_residual_not_repaired_to_oracle() -> None:
    # Strike "15-year" but insert a WRONG replacement; the oracle says 19-year.
    pl = _plaw_bytes_with_target_and_strike(99, "10", "15-year")
    report = _build({"PL 99-3": pl})
    rows = {row.section_key: row for row in report.rows}
    row = rows["99:10"]
    assert row.row_status == "residual"
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


def test_subsection_replace_when_source_tree_parse_is_ambiguous_is_source_footing_gap() -> None:
    # Section starts with unmarked prose (a definitions/intro sentence) before the
    # first enumerated marker, so the source-tree split emits an ambiguity finding
    # even though the paragraph level exists. The target label is missing, but the
    # gap is in the source tree, not the lowering.
    section = synthetic_usc_section(
        title=50,
        section="1881a",
        text=(
            "Notwithstanding any other provision of law, upon the issuance... "
            "(1) may not intentionally target any person known at the time of acquisition to be located in the United States. "
            "(2) may not intentionally target a person reasonably believed to be located outside the United States."
        ),
    )
    op = LegalOperation(
        op_id="replace-para-5",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(
            path=(
                ("title", "50"),
                ("section", "1881a"),
                ("paragraph", "5"),
            )
        ),
        payload=IRNode(
            kind=IRNodeKind.SUBSECTION,
            label="5",
            text="(5) Replacement paragraph.",
        ),
    )
    outcome = _materialize_one(
        op, section.statutory_text, before_section=section
    )
    assert not isinstance(outcome, USDryRunRefusal)
    _materialized, rule_id, disposition = outcome
    assert rule_id == US_DRY_RUN_RESIDUAL_SOURCE_TREE_PARSE_AMBIGUOUS_RULE_ID
    assert disposition == DISPOSITION_MISSING_SOURCE


def test_source_tree_parse_ambiguous_not_fired_when_parse_is_clean_and_label_missing() -> None:
    # Control: section has clean markers, target level exists, label is missing.
    # No source-tree ambiguity, so the generic node-not-located residual stays.
    section = synthetic_usc_section(
        title=11,
        section="77",
        text="(1) The first paragraph. (2) The second paragraph.",
    )
    op = LegalOperation(
        op_id="replace-para-99",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(
            path=(
                ("title", "11"),
                ("section", "77"),
                ("paragraph", "99"),
            )
        ),
        payload=IRNode(
            kind=IRNodeKind.SUBSECTION,
            label="99",
            text="(99) Replacement paragraph.",
        ),
    )
    outcome = _materialize_one(
        op, section.statutory_text, before_section=section
    )
    assert not isinstance(outcome, USDryRunRefusal)
    _materialized, rule_id, disposition = outcome
    assert rule_id == US_DRY_RUN_RESIDUAL_SUBSECTION_NODE_NOT_LOCATED_RULE_ID
    assert disposition == DISPOSITION_LAWVM_WRONG


# ---------------------------------------------------------------------------
# Suffix-match recovery for bare-leaf sub-section targets (§0 owned heuristic,
# family ``target_resolution_recovery``).
#
# When the amendatory lowerer emits a target address with a sub-section segment
# (paragraph/subparagraph/clause) but WITHOUT a parent subsection segment —
# ``title:10/section:2432/paragraph:1`` instead of the full
# ``title:10/section:2432/subsection:b/paragraph:1`` — the materializer's strict
# equality matcher (``_locate_subsection_text``) used to refuse the op as
# ``us_dry_run_residual_subsection_target_node_not_located_in_before_section``
# even when the targeted node was unambiguously present in the source-tree split.
# The suffix-match fallback in ``_locate_subsection_text_resolved`` recovers the
# unambiguous parent the lowerer dropped when EXACTLY ONE source-tree node ends
# with the target's segments; multiple matches still refuse (§1.1 no silent target
# hijacking). The recovery is owned by
# ``US_DRY_RUN_RECOVERED_BARE_LEAF_TARGET_VIA_UNIQUE_SUFFIX_RULE_ID`` and surfaces
# as a typed ``USDryRunTargetRecovery`` witness on the report.
# ---------------------------------------------------------------------------


def _bare_leaf_unique_match_section() -> UscSection:
    """Section whose only ``paragraph:1`` lives under ``subsection:b``.

    Subsection (a) has no paragraphs; only (b) carries (1) and (2). So a bare-leaf
    ``paragraph:1`` target (no parent subsection prefix) has a UNIQUE source-tree
    match ending with ``paragraph:1`` — exactly the bare-leaf shape on the title 10
    2018->2020 §2432 family of amendments.
    """
    return synthetic_usc_section(
        title=10,
        section="2432",
        text=(
            "(a) Subsection A has no paragraphs. "
            "(b) Authority is granted. "
            "(1) The first paragraph mentions a 15-year window. "
            "(2) The second paragraph stands alone."
        ),
    )


def test_locate_subsection_text_resolved_returns_none_when_strict_match_present() -> None:
    # Sanity: when the address names the full path ``.../subsection:b/paragraph:1``,
    # the strict-equality matcher resolves without firing the recovery. The resolved
    # segments equal the target segments (the §0 recovery signal must stay silent).
    section = _bare_leaf_unique_match_section()
    full = LegalAddress(
        path=(
            ("title", "10"),
            ("section", "2432"),
            ("subsection", "b"),
            ("paragraph", "1"),
        )
    )
    resolved = _locate_subsection_text_resolved(section, full)
    assert resolved is not None
    assert resolved.text == "(1) The first paragraph mentions a 15-year window."
    assert resolved.resolved_segments == _subsection_segments(full)


def test_locate_subsection_text_resolved_recovers_unique_bare_leaf_via_suffix_match() -> None:
    # The lowerer emitted a bare-leaf address (no parent ``subsection:b``
    # prefix). The strict-equality matcher would refuse; the suffix-match
    # fallback finds exactly one source-tree node ending with ``paragraph:1``
    # (the one under subsection (b)) and recovers the parent. The resolved
    # segments carry the recovered ancestor (the witness for §0 ownership).
    section = _bare_leaf_unique_match_section()
    bare_leaf = LegalAddress(
        path=(("title", "10"), ("section", "2432"), ("paragraph", "1"))
    )
    resolved = _locate_subsection_text_resolved(section, bare_leaf)
    assert resolved is not None
    assert resolved.text == "(1) The first paragraph mentions a 15-year window."
    # The recovered ancestor is the missing subsection:b prefix.
    assert resolved.resolved_segments == (("subsection", "b"), ("paragraph", "1"))
    assert resolved.resolved_segments != _subsection_segments(bare_leaf)


def test_locate_subsection_text_resolved_refuses_when_suffix_match_is_ambiguous() -> None:
    # §1.1 firewall: when MORE THAN ONE source-tree node ends with the target's
    # segments (paragraph:1 under both subsection:a and subsection:b), the
    # recovery MUST refuse rather than hijack one. The bare-leaf target stays
    # unlocated and the existing typed residual path takes over at the caller.
    section = synthetic_usc_section(
        title=10,
        section="2432-amb",
        text=(
            "(a) Subsection A. "
            "(1) Paragraph A1. "
            "(b) Subsection B. "
            "(1) Paragraph B1. "
            "(2) Paragraph B2."
        ),
    )
    bare_leaf = LegalAddress(
        path=(("title", "10"), ("section", "2432-amb"), ("paragraph", "1"))
    )
    resolved = _locate_subsection_text_resolved(section, bare_leaf)
    assert resolved is None


def test_locate_subsection_text_backwards_compatible_wrapper_still_returns_text() -> None:
    # The deprecated single-string wrapper still returns the node text on both a
    # strict match and a recovery; existing direct callers (no evidence path) keep
    # working byte-for-byte. Tests that import ``_locate_subsection_text`` should
    # not need to call ``_locate_subsection_text_resolved`` to keep existing
    # assertions passing.
    section = _bare_leaf_unique_match_section()
    bare_leaf = LegalAddress(
        path=(("title", "10"), ("section", "2432"), ("paragraph", "1"))
    )
    text = _locate_subsection_text(section, bare_leaf)
    assert text == "(1) The first paragraph mentions a 15-year window."


def test_text_replace_on_bare_leaf_target_materializes_via_suffix_match_recovery() -> None:
    # End-to-end: a TEXT_REPLACE op with a bare-leaf ``paragraph:1`` target is
    # applied to the UNIQUE source-tree node the recovery resolves to. Without
    # the fix this op would emit
    # ``us_dry_run_residual_subsection_target_node_not_located_in_before_section``
    # (lawvm_wrong). With the fix the patch lands inside subsection (b)'s
    # paragraph (1) text only — never widens onto the whole section, never
    # hijacks a sibling — and the recovery observation is emitted with its
    # witness (op_id, target_segments, resolved_node_segments).
    section = _bare_leaf_unique_match_section()
    op = LegalOperation(
        op_id="strike-15y-in-para-1",
        sequence=1,
        action=StructuralAction.TEXT_PATCH,
        target=LegalAddress(
            path=(("title", "10"), ("section", "2432"), ("paragraph", "1"))
        ),
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector(match_text="15-year", occurrence=1),
            replacement="20-year",
        ),
    )
    recoveries: list[USDryRunTargetRecovery] = []
    outcome = _materialize_one(
        op,
        section.statutory_text,
        before_section=section,
        recoveries=recoveries,
    )
    assert not isinstance(outcome, USDryRunRefusal), outcome
    materialized, rule_id, _disposition = outcome
    assert rule_id == ""  # recovery is non-blocking: the patch landed.
    # Only the targeted paragraph (1) under subsection (b) was rewritten.
    assert "(1) The first paragraph mentions a 20-year window." in materialized
    assert "15-year" not in materialized
    # Subsection (a) prose and paragraph (2) survived untouched (no hijack).
    assert "(a) Subsection A has no paragraphs." in materialized
    assert "(2) The second paragraph stands alone." in materialized

    # The §0 typed emission carries the witness.
    assert len(recoveries) == 1
    recovery = recoveries[0]
    assert recovery.op_id == "strike-15y-in-para-1"
    assert recovery.target_segments == (("paragraph", "1"),)
    assert recovery.resolved_node_segments == (("subsection", "b"), ("paragraph", "1"))
    assert recovery.family == "target_resolution_recovery"
    assert recovery.to_jsonable()["rule_id"] == (
        US_DRY_RUN_RECOVERED_BARE_LEAF_TARGET_VIA_UNIQUE_SUFFIX_RULE_ID
    )


def test_text_replace_on_bare_leaf_target_refuses_when_suffix_match_ambiguous() -> None:
    # §1.1 firewall at the materializer: when the suffix match is ambiguous (two
    # distinct ``paragraph:1`` source-tree nodes), the materializer MUST NOT
    # hijack one. The recovery observation list stays empty and the existing
    # typed residual fires (lawvm_wrong — the lowerer's bare-leaf address was
    # genuinely under-specified for this section shape).
    section = synthetic_usc_section(
        title=10,
        section="2432-amb",
        text=(
            "(a) Subsection A. "
            "(1) Paragraph A1 has the 15-year anchor. "
            "(b) Subsection B. "
            "(1) Paragraph B1 has the 15-year anchor too. "
            "(2) Paragraph B2."
        ),
    )
    op = LegalOperation(
        op_id="strike-15y-in-para-1-ambiguous",
        sequence=1,
        action=StructuralAction.TEXT_PATCH,
        target=LegalAddress(
            path=(("title", "10"), ("section", "2432-amb"), ("paragraph", "1"))
        ),
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector(match_text="15-year", occurrence=1),
            replacement="20-year",
        ),
    )
    recoveries: list[USDryRunTargetRecovery] = []
    outcome = _materialize_one(
        op,
        section.statutory_text,
        before_section=section,
        recoveries=recoveries,
    )
    assert not isinstance(outcome, USDryRunRefusal)
    _materialized, rule_id, disposition = outcome
    assert rule_id == US_DRY_RUN_RESIDUAL_SUBSECTION_NODE_NOT_LOCATED_RULE_ID
    assert disposition == DISPOSITION_LAWVM_WRONG
    assert recoveries == []


def test_repeated_bare_leaf_patches_compose_via_resolved_node_overrides() -> None:
    # When a prior op against the SAME resolved node wrote its new text under the
    # resolved (full) segments key — e.g. an op with a fully qualified target
    # ``subsection:b/paragraph:1`` followed by a bare-leaf ``paragraph:1`` op —
    # the bare-leaf op must act on the RUNNING text the prior patch produced,
    # not the now-stale pristine before-edition span. This is the multi-patch
    # composition the recovery must keep intact.
    section = _bare_leaf_unique_match_section()
    full_target = LegalAddress(
        path=(
            ("title", "10"),
            ("section", "2432"),
            ("subsection", "b"),
            ("paragraph", "1"),
        )
    )
    bare_leaf = LegalAddress(
        path=(("title", "10"), ("section", "2432"), ("paragraph", "1"))
    )
    op_full = LegalOperation(
        op_id="patch-1-full",
        sequence=1,
        action=StructuralAction.TEXT_PATCH,
        target=full_target,
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector(match_text="15-year", occurrence=1),
            replacement="20-year",
        ),
    )
    op_bare = LegalOperation(
        op_id="patch-2-bare",
        sequence=2,
        action=StructuralAction.TEXT_PATCH,
        target=bare_leaf,
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector(match_text="20-year", occurrence=1),
            replacement="25-year",
        ),
    )
    recoveries: list[USDryRunTargetRecovery] = []
    seeded_overrides: dict[tuple[tuple[str, str], ...], str] = {}
    outcome1 = _materialize_one(
        op_full,
        section.statutory_text,
        before_section=section,
        node_overrides=seeded_overrides,
        recoveries=recoveries,
    )
    assert not isinstance(outcome1, USDryRunRefusal), outcome1
    materialized_1, rule_1, _ = outcome1
    assert rule_1 == ""
    assert "(1) The first paragraph mentions a 20-year window." in materialized_1
    # The first op had a fully-specified address: no recovery.
    assert recoveries == []

    outcome2 = _materialize_one(
        op_bare,
        materialized_1,
        before_section=section,
        node_overrides=seeded_overrides,
        recoveries=recoveries,
    )
    assert not isinstance(outcome2, USDryRunRefusal), outcome2
    materialized_2, rule_2, _ = outcome2
    assert rule_2 == ""
    # The bare-leaf patch composed onto the prior op's text — not the stale
    # pristine ``15-year`` text that is no longer in the running composition.
    assert "(1) The first paragraph mentions a 25-year window." in materialized_2
    assert "20-year" not in materialized_2
    assert "15-year" not in materialized_2
    # The §0 typed emission fired for the bare-leaf op (the witness is preserved).
    assert len(recoveries) == 1
    assert recoveries[0].op_id == "patch-2-bare"
    assert recoveries[0].target_segments == (("paragraph", "1"),)
    assert recoveries[0].resolved_node_segments == (
        ("subsection", "b"),
        ("paragraph", "1"),
    )


def test_build_us_dry_run_carries_target_recoveries_through_the_report() -> None:
    # The §0 ownership surface: when the suffix-match recovery fires inside
    # ``_materialize_one``, the typed ``USDryRunTargetRecovery`` observation MUST
    # reach the public ``USDryRunReport`` and serialize via ``to_jsonable``/``summary``
    # so the heuristic cannot go invisible (AGENTS.md §0 forbids invisible
    # heuristics). We exercise the wiring directly: build a USDryRunReport with a
    # pre-built recovery entry, then assert the serialization carries the witness.
    recovery = USDryRunTargetRecovery(
        op_id="test-op",
        target_address="title:10/section:2432/paragraph:1",
        target_segments=(("paragraph", "1"),),
        resolved_node_segments=(("subsection", "b"), ("paragraph", "1")),
    )
    # ``boundary_proof`` defaults to the empty-proof (no oracle/claimed sets); the
    # minimal report construction is sufficient to exercise serialization.
    boundary = _build().boundary_proof
    report = USDryRunReport(
        title=10,
        before_year="2023",
        after_year="2024",
        statute_ids=("PL 99-9",),
        rows=(),
        refusals=(),
        oracle_changed_sections=(),
        claimed_sections=(),
        boundary_proof=boundary,
        target_recoveries=(recovery,),
    )
    assert report.target_recoveries == (recovery,)
    payload = report.to_jsonable()
    assert payload["summary"]["target_recovery_count"] == 1
    assert len(payload["target_recoveries"]) == 1
    serialized = payload["target_recoveries"][0]
    assert serialized["rule_id"] == (
        US_DRY_RUN_RECOVERED_BARE_LEAF_TARGET_VIA_UNIQUE_SUFFIX_RULE_ID
    )
    assert serialized["family"] == "target_resolution_recovery"
    assert serialized["op_id"] == "test-op"
    assert serialized["target_address"] == "title:10/section:2432/paragraph:1"
    # The witness segments serialize as JSON arrays of ``[kind, label]`` pairs.
    assert serialized["target_segments"] == [["paragraph", "1"]]
    assert serialized["resolved_node_segments"] == [
        ["subsection", "b"],
        ["paragraph", "1"],
    ]
    # ``replay_authorized`` stays False: the recovery cannot authorize replay.
    assert report.replay_authorized is False
    assert payload["replay_authorized"] is False


def test_build_us_dry_run_default_report_has_no_target_recoveries() -> None:
    # Regression guard: the existing synthetic Title 99 fixture lowers no bare-leaf
    # sub-section targets, so its report carries zero target_recoveries — the
    # shipping path must not silently emit observations no recovery fired.
    report = _build()
    assert report.target_recoveries == ()
    assert report.to_jsonable()["summary"]["target_recovery_count"] == 0
    assert report.to_jsonable()["target_recoveries"] == []


def test_build_us_dry_run_carries_same_moment_findings_through_the_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard-liveness: same-moment findings survive the full dry-run report path."""

    class _LoweredReport:
        def __init__(self, op: LegalOperation) -> None:
            self.enacted = op.source.enacted if op.source is not None else ""
            self.instructions = ()
            self._op = op

        def operations(self) -> tuple[LegalOperation, ...]:
            return (self._op,)

    def _replace_op(statute_id: str, text: str) -> LegalOperation:
        section = "10"
        return LegalOperation(
            op_id=f"{statute_id}#replace-10",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("title", "99"), ("section", section))),
            payload=IRNode(kind=IRNodeKind.SECTION, label=section, text=text),
            source=OperationSource(
                statute_id=statute_id,
                enacted="2024-01-01",
                effective="",
            ),
        )

    lowered = {
        "PL 99-100": _LoweredReport(_replace_op("PL 99-100", "Alpha body.")),
        "PL 99-101": _LoweredReport(_replace_op("PL 99-101", "Beta body.")),
    }

    def _fake_lower(
        _blob: bytes,
        *,
        statute_id: str,
        enacted: str = "",
        proof_title: str = "",
        classification_index: object = None,
    ) -> _LoweredReport:
        assert enacted == ""
        assert proof_title == "99"
        assert classification_index is None
        return lowered[statute_id]

    monkeypatch.setattr(
        "lawvm.us_federal.dry_run.lower_plaw_amendatory",
        _fake_lower,
    )

    report = build_us_dry_run(
        before_htm=BEFORE_HTM,
        after_htm=AFTER_HTM,
        plaw_blobs={"PL 99-100": b"<plaw/>", "PL 99-101": b"<plaw/>"},
        title=99,
        before_year="2023",
        after_year="2024",
    )

    assert report.summary()["same_moment_finding_count"] == 1
    finding = report.same_moment_findings[0]
    assert finding.kind == US_SAME_MOMENT_CONFLICT_KIND
    assert finding.blocking is True
    assert finding.op_id == ""
    assert set(finding.detail["conflicting_affecting_acts"]) == {
        "PL 99-100",
        "PL 99-101",
    }

    payload = report.to_jsonable()
    serialized = payload["same_moment_findings"][0]
    assert payload["summary"]["same_moment_finding_count"] == 1
    assert serialized["kind"] == finding.kind
    assert serialized["blocking"] is True
    assert serialized["detail"]["effective_date"] == "2024-01-01"
    assert {op["op_id"] for op in serialized["detail"]["conflicting_ops"]} == {
        "PL 99-100#replace-10",
        "PL 99-101#replace-10",
    }


def test_sentence_surgery_finding_reclassifies_same_section_mismatch_as_missing_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard-liveness for the AIA §35:143 / §35:32 sentence-surgery family.

    The lowerer already recognizes "striking the third sentence" as a typed
    non-representable sentence-surgery finding, and "inserting between the third
    and fourth sentences" is the same positional-sentence capability gap. If
    another same-section op still claims the section and the composed text
    mismatches an oracle change, dry-run must preserve that gap as
    ``missing_source`` rather than billing the landed sibling op as
    ``lawvm_wrong``.
    """

    op = LegalOperation(
        op_id="PL 99-200#replace-10",
        sequence=1,
        action=StructuralAction.TEXT_PATCH,
        target=LegalAddress(path=(("title", "99"), ("section", "10"))),
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector(match_text="15-year", occurrence=1),
            replacement="17-year",
        ),
        source=OperationSource(
            statute_id="PL 99-200",
            enacted="2024-01-01",
            effective="",
        ),
    )
    target = LegalAddress(path=(("title", "99"), ("section", "10")))

    class _Report:
        enacted = "2024-01-01"

        def __init__(self, *, finding_rule_id: str = "") -> None:
            finding = SimpleNamespace(rule_id=finding_rule_id) if finding_rule_id else None
            self.instructions = (
                SimpleNamespace(finding=finding, target_address=target),
            ) if finding_rule_id else ()

        def operations(self) -> tuple[LegalOperation, ...]:
            return (op,)

    reports = {
        "PL 99-200": _Report(finding_rule_id=SENTENCE_STRIKE_FINDING_RULE_ID),
        "PL 99-201": _Report(),
        "PL 99-202": _Report(finding_rule_id=SENTENCE_ANCHOR_INSERT_FINDING_RULE_ID),
    }

    def _fake_lower(
        _blob: bytes,
        *,
        statute_id: str,
        enacted: str = "",
        proof_title: str = "",
        classification_index: object = None,
    ) -> _Report:
        assert enacted == ""
        assert proof_title == "99"
        assert classification_index is None
        return reports[statute_id]

    monkeypatch.setattr(
        "lawvm.us_federal.dry_run.lower_plaw_amendatory",
        _fake_lower,
    )
    with_finding = build_us_dry_run(
        before_htm=BEFORE_HTM,
        after_htm=AFTER_HTM,
        plaw_blobs={"PL 99-200": b"<uslm/>"},
        title=99,
        before_year="2023",
        after_year="2024",
    )
    without_finding = build_us_dry_run(
        before_htm=BEFORE_HTM,
        after_htm=AFTER_HTM,
        plaw_blobs={"PL 99-201": b"<uslm/>"},
        title=99,
        before_year="2023",
        after_year="2024",
    )
    with_sentence_insert = build_us_dry_run(
        before_htm=BEFORE_HTM,
        after_htm=AFTER_HTM,
        plaw_blobs={"PL 99-202": b"<uslm/>"},
        title=99,
        before_year="2023",
        after_year="2024",
    )

    row = {r.section_key: r for r in with_finding.rows}["99:10"]
    assert row.row_status == "residual"
    assert row.rule_id == SENTENCE_STRIKE_FINDING_RULE_ID
    assert row.disposition == DISPOSITION_MISSING_SOURCE
    assert with_finding.summary()["lowering_sentence_strike_sections"] == ["99:10"]
    assert "99:10" in with_finding.north_star()["missing_source_sections"]

    insert_row = {r.section_key: r for r in with_sentence_insert.rows}["99:10"]
    assert insert_row.row_status == "residual"
    assert insert_row.rule_id == SENTENCE_ANCHOR_INSERT_FINDING_RULE_ID
    assert insert_row.disposition == DISPOSITION_MISSING_SOURCE
    assert with_sentence_insert.summary()["lowering_sentence_strike_sections"] == ["99:10"]
    assert "99:10" in with_sentence_insert.north_star()["missing_source_sections"]

    negative = {r.section_key: r for r in without_finding.rows}["99:10"]
    assert negative.row_status == "residual"
    assert negative.rule_id == US_DRY_RUN_RESIDUAL_TEXT_MISMATCH_RULE_ID
    assert negative.disposition == DISPOSITION_LAWVM_WRONG


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


def _build_real_title23_2020_2022_report_or_skip() -> USDryRunReport:
    from lawvm.tools.us_anchor_manifest import _repo_root
    from lawvm.us_federal.bench import (
        DEFAULT_CORPUS_PATH,
        derive_window_law_locators,
        load_corpus,
    )
    from lawvm.us_federal.dry_run import build_us_dry_run_from_archive
    from lawvm.us_federal.sources import (
        open_us_federal_farchive,
        resolve_us_federal_farchive_path,
    )

    archive_path, _rule = resolve_us_federal_farchive_path()
    if not archive_path.exists():
        pytest.skip("us_federal.farchive absent; Title 23 dry-run witness unavailable")

    window = next(
        w
        for w in load_corpus(_repo_root() / DEFAULT_CORPUS_PATH)
        if w.key == "title23:2020->2022"
    )
    archive = open_us_federal_farchive(readonly=True)
    try:
        locators = derive_window_law_locators(
            archive,
            title=window.title,
            before_year=window.before_year,
            after_year=window.after_year,
        )
        report = build_us_dry_run_from_archive(
            archive,
            title=window.title,
            before_year=window.before_year,
            after_year=window.after_year,
            plaw_locators=locators or {},
            prior_edition_years=window.prior_edition_years,
        )
    finally:
        archive.close()
    return report


def test_real_title23_sentence_scoped_punct_insert_is_missing_source_when_archive_present() -> None:
    report = _build_real_title23_2020_2022_report_or_skip()

    rows = {r.section_key: r for r in report.rows}
    assert "23:140" not in rows
    assert "23:140" not in report.claimed_sections
    assert "23:140" in report.north_star()["missing_source_sections"]
    assert "23:140" in report.summary()["lowering_sentence_strike_sections"]


def test_real_title23_insert_after_subsection_tail_is_oracle_suspect_when_archive_present() -> None:
    report = _build_real_title23_2020_2022_report_or_skip()

    row = {r.section_key: r for r in report.rows}["23:313"]
    assert row.rule_id == US_DRY_RUN_RESIDUAL_TEXT_MISMATCH_RULE_ID
    assert row.disposition == DISPOSITION_ORACLE_SUSPECT
    assert (
        "the provisions of subsection (b) shall not apply to products produced "
        "in that foreign country. (g) Waivers"
    ) in row.materialized_text
    assert _norm_editorial(row.materialized_text) == _norm_editorial(row.oracle_text)


def test_real_title23_section102_structural_payload_phrase_swap_agrees_when_archive_present() -> None:
    report = _build_real_title23_2020_2022_report_or_skip()

    row = {r.section_key: r for r in report.rows}["23:102"]
    assert row.row_status == "agree"
    assert row.rule_id == US_DRY_RUN_SECTION_AGREES_RULE_ID
    assert row.disposition == ""
    assert "(a) (b) Savings Provision" not in row.materialized_text
    assert "(a) Access of Motorcycles" in row.materialized_text
    assert "(b) Savings Provision" in row.materialized_text


def test_real_title23_section109_heading_body_duplicate_anchor_is_oracle_suspect_when_archive_present() -> None:
    report = _build_real_title23_2020_2022_report_or_skip()

    row = {r.section_key: r for r in report.rows}["23:109"]
    assert row.rule_id == US_DRY_RUN_RESIDUAL_TEXT_MISMATCH_RULE_ID
    assert row.disposition == DISPOSITION_ORACLE_SUSPECT
    assert "Non-NHS Projects.—(A) In general.—Projects" in row.materialized_text
    assert "Non-NHS (A) In general.—Projects.—Projects" not in row.materialized_text
    assert _norm_editorial(row.materialized_text) == _norm_editorial(row.oracle_text)


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
def test_real_title11_pl118_42_window_507d_insert_after_anchor_materializes_in_agreement() -> None:
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
    # RESOLVED DIVERGENCE (formerly test_..._stays_oracle_suspect_courtesy_space):
    # the enacted instruction inserts "excluding subparagraph (F)" after the anchor
    # "(a)(8)" in the running body "...(a)(7), (a)(8), or (a)(9)...". Earlier lowering
    # spliced the phrase directly onto the ")" with NO separator ("(a)(8)excluding"),
    # so the section could only reach the published Code's "(a)(8) excluding" through
    # the editorial insert-after-anchor space projection (an oracle_suspect residual).
    # The amendatory boundary rule was subsequently corrected (`_join_insert_after`:
    # a ")"/"," that ENDS the anchor token followed by a fresh word IS a genuine
    # word-junction taking one separating space — data-backed by 38:4303's
    # "Public Health Service," + "System members" -> "Public Health Service, System
    # members"). The separating space is now recognized as the ENACTED result, not an
    # invented OLRC courtesy space, so §507(d) materializes byte-equal to the oracle
    # with no comparison projection. The whole 9,779-byte section agrees; this is a
    # genuine (not forced) full-section match, and the "stays oracle_suspect" framing
    # is retired. No repair-to-oracle: the space is produced by faithful lowering.
    rows = {row.section_key: row for row in report.rows}
    row = rows["11:507"]
    assert row.row_status == "agree"
    assert row.disposition == ""
    assert "(a)(8) excluding subparagraph (F)" in row.materialized_text
    assert "(a)(8) excluding subparagraph (F)" in row.oracle_text
    # Byte-identical materialization, not merely a projected agreement.
    assert row.materialized_text == row.oracle_text
    # Sections 109 and 1182 are NOT missing-source gaps: the F2 temporal layer
    # reclassifies them as sunset reversions (the SBRA debt-limit increase
    # sunset on June 21, 2024). The note-based channel (b) fires here even without
    # prior editions being loaded, so missing_source is empty.
    ns = report.north_star()
    assert ns["oracle_changed_section_count"] == 3
    assert ns["sections_materialized_in_agreement"] == 1
    assert set(ns["missing_source_sections"]) == set()
    assert set(ns["sunset_reversion_sections"]) == {"11:109", "11:1182"}
    rev_by_section = {c.section: c for c in report.sunset_reversions}
    assert set(rev_by_section) == {"109", "1182"}
    assert rev_by_section["109"].witness.sunset_date == "2024-06-21"
    assert rev_by_section["1182"].witness.sunset_date == "2024-06-21"
    # The gate stays closed.
    assert report.replay_authorized is False


@pytest.mark.skipif(
    not _canonical_archive_available(),
    reason="canonical us_federal.farchive not linked (LAWVM_CANONICAL_DATA_ROOT unset)",
)
def test_shared_mid_chain_resolver_does_not_tank_compound_chain_sections() -> None:
    """F2 shared mid-chain resolver (DEFERRED_ROADMAP §F, title23:2020->2022, IIJA).

    A compound amendment chain (insert+replace+renumber+repeal in source order) can
    carry ONE mid-chain op whose sub-section target cannot be located against the
    running composition — its foundation RENUMBER/strike was refused as absent from
    this window's before edition (an un-lowered foundation op). Historically that op's
    empty ("") return BROKE the per-section loop, DISCARDING every correctly-composed
    sibling op and tanking the whole section to an empty ``lawvm_wrong`` residual.

    The shared resolver instead SKIPS the single unresolvable op and CONTINUES composing
    the rest of the chain. This regression pins that §133/§148/§515 (title23) and §41
    (title35) no longer materialize an EMPTY, section-tanking ``lawvm_wrong``: the
    surviving composition is preserved and the section is a typed non-billable residual
    (a partial-composition capability gap ``missing_source``, or the source-truncated /
    deferred_op class the composed text legitimately falls into), never a wrong empty.
    """
    from lawvm.us_federal.bench import (
        DEFAULT_CORPUS_PATH,
        evaluate_window,
        load_corpus,
        open_us_federal_farchive,
    )
    from lawvm.tools.us_anchor_manifest import _repo_root

    corpus = {w.key: w for w in load_corpus(_repo_root() / DEFAULT_CORPUS_PATH)}
    archive = open_us_federal_farchive(readonly=True)
    try:
        checks = {
            "title23:2020->2022": ("23:133", "23:148", "23:515"),
            "title35:2010->2012": ("35:41",),
        }
        for window_key, sections in checks.items():
            report = evaluate_window(archive, corpus[window_key]).report
            assert report is not None, f"{window_key} did not evaluate to a report"
            rows = {r.section_key: r for r in report.rows}
            for sec in sections:
                assert sec in rows, f"{window_key} {sec} not composed at all"
                row = rows[sec]
                # NEVER an empty section-tanking lawvm_wrong: the composed text is
                # preserved and the disposition is non-billable.
                assert not (
                    row.materialized_text == ""
                    and row.disposition == DISPOSITION_LAWVM_WRONG
                ), f"{window_key} {sec} still tanks to an empty lawvm_wrong"
                assert row.disposition != DISPOSITION_LAWVM_WRONG, (
                    f"{window_key} {sec} is billable lawvm_wrong; the shared mid-chain "
                    f"resolver should have diverted it to a non-billable typed residual"
                )
    finally:
        archive.close()


def test_partial_composition_mid_chain_gap_rule_id_is_cataloged() -> None:
    """The shared-resolver capability-gap rule id is registered in the US spec ledger
    (its emit site is convicted by the AST catalog-completeness test; this pins the
    description entry exists so the producer/consumer sets stay reconciled)."""
    from lawvm.tools.spec_ledger_us_catalog import _US_RULE_SPECS

    assert (
        US_DRY_RUN_RESIDUAL_PARTIAL_COMPOSITION_MID_CHAIN_GAP_RULE_ID in _US_RULE_SPECS
    )
    assert _US_RULE_SPECS[
        US_DRY_RUN_RESIDUAL_PARTIAL_COMPOSITION_MID_CHAIN_GAP_RULE_ID
    ].strip()


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
    # edit is scoped to the targeted node located by the pinned address convention,
    # and synthetic child nodes introduced by earlier sibling ops are indexed so
    # later amendments can locate them.  No composition phase now fails with the
    # `subsection_target_node_not_located` residual for this window.
    node_not_located = [
        r
        for r in report.residual_rows()
        if r.rule_id == US_DRY_RUN_RESIDUAL_SUBSECTION_NODE_NOT_LOCATED_RULE_ID
    ]
    assert not node_not_located, "SBRA structural redesignations should compose without node-not-located residuals"
    # §101 is amended by FIVE window ops (116-51/52/92/136); they compose into ONE
    # row, not five. (PL 116-51's each-place debt-limit strike materializes; the
    # SBRA/PL 116-136 structural redesignations now compose.)
    s101_rows = [r for r in report.rows if r.section_key == "11:101"]
    assert len(s101_rows) == 1
    # Every published residual is typed (no blank disposition) and the gate is shut.
    for row in report.residual_rows():
        assert row.disposition in (
            DISPOSITION_LAWVM_WRONG,
            DISPOSITION_ORACLE_SUSPECT,
        )
    assert report.replay_authorized is False

    # Residual regression fence: these sections previously fell into lawvm_wrong
    # because of redesignate-range mis-typing, mis-applied conditional/sunset
    # effective scopes, or container-level token replacement missing the deeper
    # descendants (§365's 120->210 strike in paragraph (4) only reaches the
    # actual occurrences through subparagraph/clause nodes).
    lawvm_wrong_sections = {
        r.section_key
        for r in report.rows
        if r.disposition == DISPOSITION_LAWVM_WRONG
    }
    assert "11:103" not in lawvm_wrong_sections
    assert "11:503" not in lawvm_wrong_sections
    assert "11:1182" not in lawvm_wrong_sections
    assert "11:365" not in lawvm_wrong_sections
    assert "11:541" not in lawvm_wrong_sections

    # §101 had a remaining lawvm_wrong caused by a USLM XML-truncated SBRA
    # redesignation clause (PL 116-54 /s4/a/1/B/ii/II: "(i) any member").  It is
    # now classified as oracle_suspect under the source-truncated-payload rule
    # rather than lawvm_wrong, because our materialization is source-faithful.
    assert "11:101" not in lawvm_wrong_sections
    s101 = {r.section_key: r for r in report.rows}.get("11:101")
    assert s101 is not None
    assert s101.disposition == DISPOSITION_ORACLE_SUSPECT
    assert s101.rule_id == US_DRY_RUN_RESIDUAL_SOURCE_TRUNCATED_PAYLOAD_RULE_ID

    # Oracle-suspect residuals are now numerous (editorial quote shapes, dropped
    # marginal notes, etc.). We only assert a stable floor rather than pinning a
    # single section: the exact section representative can shift as composition
    # improves.
    disp = summary["residual_disposition_counts"]
    assert disp.get(DISPOSITION_ORACLE_SUSPECT, 0) >= 3
    # §1329 is now a match up to OLRC editorial projection: the PL 116-136 insert
    # of subsection (d) and the PL 116-260 insert of subsection (e) both carry
    # future effective dates (1 year after enactment for the (d) sunset package;
    # the (e) package is also after 2020-12-31). The temporal guard defers the
    # sunset strike that would remove (d), so the materialized text keeps both
    # temporary subsections and agrees with the 2020 after-edition once quote/shape
    # differences are classified as oracle editorial.
    s1329 = {r.section_key: r for r in report.rows}.get("11:1329")
    assert s1329 is not None
    assert s1329.disposition == DISPOSITION_ORACLE_SUSPECT
    # §547(b) (PL 116-54 §3(a)) is "inserting '<due-diligence clause>' after 'may'"
    # with NO striking — an insert-after, not a strike_insert. The lowering now
    # classifies it correctly and finds the anchor "may" in the 2018 edition, so it
    # is no longer a match-not-found residual. It stays a residual only because the
    # section also acquired a "(j)" subsection via another window amendment not
    # lowered here — honest incompleteness, never repaired to the oracle.
    s547 = {r.section_key: r for r in report.rows}.get("11:547")
    assert s547 is not None
    assert s547.rule_id == US_DRY_RUN_RESIDUAL_TEXT_MISMATCH_RULE_ID
    # The insert-after materialization and the (j) subsection quote-shape differences
    # both fall under OLRC editorial projection once quote/spacing normalization is
    # applied, so the residual is oracle_suspect rather than lawvm_wrong.
    assert s547.disposition == DISPOSITION_ORACLE_SUSPECT
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
    # PL 116-260 contains future-effective provisions (e.g. §502(b)(9)(C)) whose
    # effective date is 2021-12-27. They must not be applied against the 2020
    # after-edition, so they surface as typed deferred refusals rather than
    # lawvm_wrong materializations. (§541's structural composition gap was fixed
    # separately by anchoring the paragraph (11) insertion after the full paragraph
    # (10) subtree, including its subparagraphs.)
    deferred = [
        r
        for r in report.refusals
        if r.rule_id == US_DRY_RUN_REFUSED_DEFERRED_OP_NOT_YET_EFFECTIVE_RULE_ID
    ]
    assert deferred, "expected deferred-op refusals for PL 116-260 future-effective provisions"
    for section in ("502",):
        row = {r.section_key: r for r in report.rows}.get(f"11:{section}")
        if row is not None:
            assert row.disposition != DISPOSITION_LAWVM_WRONG, (
                f"{section} should not stay lawvm_wrong after delayed ops are skipped"
            )


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
    assert op.action is StructuralAction.TEXT_PATCH
    assert op.text_patch is not None
    assert op.text_patch.kind is TextPatchKindEnum.REPLACE


@pytest.mark.xfail(
    reason="Pre-existing target-resolution gap: PL 116-283 §501(c)(2) strike_insert for 10 U.S.C. 526(b)(3)(A) resolves with empty phrase/href and is unlowered.",
)
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
    assert row.row_status == "residual"
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
    # source-footing-gap residual, exactly the Prime-Directive-safe refusal.
    #
    # SHARED MID-CHAIN RESOLVER (this session): §3222a is a partial-composition chain —
    # its op1 (a clean text_patch on the (a)(3) paragraph) lands, then op2/op3 (an
    # amend-to-read + insert on (b)(1)) carry the ``target_ancestor_absent_in_source_tree``
    # source-footing signal (the (b) ancestor is elided). Historically op2's empty return
    # BROKE the loop and DISCARDED op1's legitimate composition, emptying the section. The
    # shared resolver now SKIPS op2/op3 and PRESERVES op1's composition. The residual keeps
    # its PRECISE source-footing rule id (``target_ancestor_absent``, more informative than
    # the generic mid-chain gap) and the honest ``missing_source`` disposition; the guard
    # this test exists for — no fuzzy sibling match onto a guessed node — still holds. The
    # materialized text is now op1's non-empty composition (over-retention is the §0-safe
    # wrong), not the old empty tank.
    #
    # RESIDUAL RE-TYPING (was ``subsection_target_node_not_located`` /
    # ``lawvm_wrong``): the refusal is unchanged — still a residual, still empty
    # materialization, still NO sibling match — but the residual is now typed more
    # honestly. The op's target ``(a)(3)`` has an ancestor subsection ``(a)`` that the
    # parsed before-section never renders (the enumerators are elided by the
    # hanging-indent style), so ``_source_tree_gap_rule_for_address`` diagnoses a
    # ``target_ancestor_absent_in_source_tree`` gap. That is a SOURCE-side footing gap,
    # not a lowering bug (we lowered the op correctly to (a)(3)); the disposition is
    # therefore the honest ``missing_source`` rather than ``lawvm_wrong``. The guard
    # this test exists for — no fuzzy sibling match, no wrong materialization — holds.
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
    assert row.row_status == "residual"
    # The precise source-footing diagnosis is preserved through the shared mid-chain
    # resolver (the ancestor-absent signal is missing_source-typed, so its exact rule id
    # survives rather than collapsing to the generic mid-chain gap).
    assert row.rule_id == US_DRY_RUN_RESIDUAL_TARGET_ANCESTOR_ABSENT_IN_SOURCE_TREE_RULE_ID
    assert row.disposition == DISPOSITION_MISSING_SOURCE
    # No wrong materialization: we did not splice a guessed sibling node. The composed
    # text is op1's legitimate (a)(3) text_patch, preserved by the shared resolver rather
    # than discarded to an empty tank (the unresolvable (b)(1) ops were skipped, not
    # fuzzy-matched). The section still diverges from the oracle (a genuine source gap).
    assert row.materialized_text != ""
    assert row.materialized_text != row.oracle_text


def test_committed_synthetic_fixtures_round_trip_through_source_tree() -> None:
    # Defence: the committed before/after htm parse to the expected section sets.
    from lawvm.us_federal.source_tree import parse_usc_title_document

    before = parse_usc_title_document(BEFORE_HTM, title=99, year="2023")
    after = parse_usc_title_document(AFTER_HTM, title=99, year="2024")
    assert [s.section for s in before.sections] == ["10", "20"]
    assert [s.section for s in after.sections] == ["10", "20", "30"]


def _title11_before_with_section_10() -> bytes:
    return (
        '<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml"><head>'
        '<title>T11 before</title><!-- AUTHORITIES-USC-TITLE-ENUM:11 --></head><body><div>'
        '<!-- expcite:TITLE 11!@!CHAPTER 1!@!Sec. 10 -->'
        '<!-- field-start:head --><h3 class="section-head">&sect;10. Existing</h3>'
        '<!-- field-end:head --><!-- field-start:statute -->'
        '<p class="statutory-body">Base body.</p>'
        '<!-- field-end:statute --></div></body></html>'
    ).encode("utf-8")


def _title11_after_with_new_section_12() -> bytes:
    return (
        '<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml"><head>'
        '<title>T11 after</title><!-- AUTHORITIES-USC-TITLE-ENUM:11 --></head><body><div>'
        '<!-- expcite:TITLE 11!@!CHAPTER 1!@!Sec. 10 -->'
        '<!-- field-start:head --><h3 class="section-head">&sect;10. Existing</h3>'
        '<!-- field-end:head --><!-- field-start:statute -->'
        '<p class="statutory-body">Base body.</p>'
        '<!-- field-end:statute -->'
        '<!-- expcite:TITLE 11!@!CHAPTER 1!@!Sec. 12 -->'
        '<!-- field-start:head --><h3 class="section-head">&sect;12. New section</h3>'
        '<!-- field-end:head --><!-- field-start:statute -->'
        '<p class="statutory-body">(a) New body.</p>'
        '<!-- field-end:statute --></div></body></html>'
    ).encode("utf-8")


def _title11_plaw_insert_section_12() -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<uslm xmlns="http://schemas.gpo.gov/xml/uslm"><meta>'
        '<congress>116</congress><docNumber>900</docNumber>'
        '<approvedDate>2024-01-01</approvedDate></meta><main>'
        '<section identifier="/us/pl/116/900/s1"><num value="1">SEC. 1. </num>'
        '<content><ref href="/us/usc/t11/c1">Chapter 1 of title 11, United '
        'States Code</ref>, <amendingAction type="amend">is amended</amendingAction> '
        'by <amendingAction type="insert">inserting</amendingAction> after section '
        '10 the following new section:<quotedContent><section><num value="12">'
        '“§ 12. </num><heading>New section</heading><content>“ (a) New body.</content>'
        '</section></quotedContent>.</content></section>'
        '</main></uslm>'
    ).encode("utf-8")


def _title11_after_with_new_section_12_and_title_strike() -> bytes:
    return (
        '<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml"><head>'
        '<title>T11 after</title><!-- AUTHORITIES-USC-TITLE-ENUM:11 --></head><body><div>'
        '<!-- expcite:TITLE 11!@!CHAPTER 1!@!Sec. 10 -->'
        '<!-- field-start:head --><h3 class="section-head">&sect;10. Existing</h3>'
        '<!-- field-end:head --><!-- field-start:statute -->'
        '<p class="statutory-body">Base body.</p>'
        '<!-- field-end:statute -->'
        '<!-- expcite:TITLE 11!@!CHAPTER 1!@!Sec. 12 -->'
        '<!-- field-start:head --><h3 class="section-head">&sect;12. New section</h3>'
        '<!-- field-end:head --><!-- field-start:statute -->'
        '<p class="statutory-body">(a) Same effect as section 252 for reissued patents.</p>'
        '<!-- field-end:statute --></div></body></html>'
    ).encode("utf-8")


def _title11_after_with_new_section_12_retaining_title_phrase() -> bytes:
    return (
        '<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml"><head>'
        '<title>T11 after</title><!-- AUTHORITIES-USC-TITLE-ENUM:11 --></head><body><div>'
        '<!-- expcite:TITLE 11!@!CHAPTER 1!@!Sec. 10 -->'
        '<!-- field-start:head --><h3 class="section-head">&sect;10. Existing</h3>'
        '<!-- field-end:head --><!-- field-start:statute -->'
        '<p class="statutory-body">Base body.</p>'
        '<!-- field-end:statute -->'
        '<!-- expcite:TITLE 11!@!CHAPTER 1!@!Sec. 12 -->'
        '<!-- field-start:head --><h3 class="section-head">&sect;12. New section</h3>'
        '<!-- field-end:head --><!-- field-start:statute -->'
        '<p class="statutory-body">(a) Same effect as section 252 of this title '
        'for reissued patents.</p>'
        '<!-- field-end:statute --></div></body></html>'
    ).encode("utf-8")


def test_new_section_insert_after_section_materializes_in_agreement() -> None:
    # A section-level insert anchored after an existing section lowers to an INSERT
    # addressed at the new section number, and the dry-run materializes it from an
    # empty before-text (the section did not exist in the before edition).
    report = build_us_dry_run(
        before_htm=_title11_before_with_section_10(),
        after_htm=_title11_after_with_new_section_12(),
        plaw_blobs={"PL 116-900": _title11_plaw_insert_section_12()},
        title=11,
        before_year="2023",
        after_year="2024",
    )
    assert "11:12" in report.claimed_sections
    rows = {row.section_key: row for row in report.rows}
    assert "11:12" in rows
    row = rows["11:12"]
    assert row.row_status == "agree"
    assert row.rule_id == US_DRY_RUN_SECTION_AGREES_RULE_ID
    assert "(a) New body." in row.materialized_text
    ns = report.north_star()
    assert ns["oracle_changed_section_count"] == 1
    assert ns["sections_materialized_in_agreement"] == 1
    assert ns["coverage_fraction"] == pytest.approx(1.0)
    assert ns["missing_source_sections"] == []


def test_title_each_place_strike_reaches_same_window_inserted_section(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # AIA §6 + §20(j) witness shape: a section is inserted earlier in the same
    # window with an internal "of this title" cross-reference, then a later
    # title-wide each-place strike removes that term. The container fan-out must
    # include same-window created sections after their creating INSERT, not only
    # sections present in the before edition.
    insert_op = LegalOperation(
        op_id="insert-s12",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("title", "11"), ("section", "12"))),
        payload=IRNode(
            kind=IRNodeKind.SECTION,
            label="12",
            text=(
                "“§ 12. New section“ (a) Same effect as section 252 of this title "
                "for reissued patents."
            ),
        ),
        source=OperationSource(
            statute_id="PL 116-901",
            enacted="2024-01-01",
            raw_text="Chapter 1 of title 11 is amended by inserting after section 10 the following new section",
        ),
    )
    title_each_place_op = LegalOperation(
        op_id="title-strike-of-this-title",
        sequence=2,
        action=StructuralAction.TEXT_PATCH,
        target=LegalAddress(path=(("title", "11"),)),
        source=OperationSource(
            statute_id="PL 116-901",
            enacted="2024-01-01",
            raw_text='Title 11, United States Code, is amended by striking "of this title" each place that term appears.',
        ),
        applicability=(
            ScopePredicate(
                dimension=US_EACH_PLACE_STRIKE_EXCEPTION_DIMENSION,
                includes=frozenset(),
            ),
        ),
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.DELETE,
            selector=TextSelector(match_text="of this title", occurrence=-1),
        ),
    )

    class _Report:
        enacted = "2024-01-01"
        findings = ()
        title_targets = ("title 11",)

        @staticmethod
        def operations() -> tuple[LegalOperation, ...]:
            return (insert_op, title_each_place_op)

    monkeypatch.setattr(
        "lawvm.us_federal.dry_run.lower_plaw_amendatory",
        lambda *_args, **_kwargs: _Report(),
    )

    report = build_us_dry_run(
        before_htm=_title11_before_with_section_10(),
        after_htm=_title11_after_with_new_section_12_and_title_strike(),
        plaw_blobs={"PL 116-901": b"<lawDoc/>"},
        title=11,
        before_year="2023",
        after_year="2024",
    )

    row = {row.section_key: row for row in report.rows}["11:12"]
    assert row.row_status == "agree"
    assert row.rule_id == US_DRY_RUN_SECTION_AGREES_RULE_ID
    assert "section 252 for reissued patents" in row.materialized_text
    assert "of this title" not in row.materialized_text
    assert "title-strike-of-this-title@s12" in row.op_id


def test_oracle_retained_same_window_title_strike_is_oracle_suspect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Same source facts as the prior test, but the oracle keeps "of this title".
    # The source-authorized title-wide strike is still applied; the retained oracle
    # phrase is classified as oracle_suspect, not as a replay bug and not by
    # suppressing the operation.
    insert_op = LegalOperation(
        op_id="insert-s12",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("title", "11"), ("section", "12"))),
        payload=IRNode(
            kind=IRNodeKind.SECTION,
            label="12",
            text=(
                "“§ 12. New section“ (a) Same effect as section 252 of this title "
                "for reissued patents."
            ),
        ),
        source=OperationSource(statute_id="PL 116-902", enacted="2024-01-01"),
    )
    title_each_place_op = LegalOperation(
        op_id="title-strike-of-this-title",
        sequence=2,
        action=StructuralAction.TEXT_PATCH,
        target=LegalAddress(path=(("title", "11"),)),
        source=OperationSource(
            statute_id="PL 116-902",
            enacted="2024-01-01",
            raw_text=(
                'Title 11, United States Code, is amended by striking "of this title" '
                "each place that term appears."
            ),
        ),
        applicability=(
            ScopePredicate(
                dimension=US_EACH_PLACE_STRIKE_EXCEPTION_DIMENSION,
                includes=frozenset(),
            ),
        ),
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.DELETE,
            selector=TextSelector(match_text="of this title", occurrence=-1),
        ),
    )

    class _Report:
        enacted = "2024-01-01"
        findings = ()
        title_targets = ("title 11",)

        @staticmethod
        def operations() -> tuple[LegalOperation, ...]:
            return (insert_op, title_each_place_op)

    monkeypatch.setattr(
        "lawvm.us_federal.dry_run.lower_plaw_amendatory",
        lambda *_args, **_kwargs: _Report(),
    )

    report = build_us_dry_run(
        before_htm=_title11_before_with_section_10(),
        after_htm=_title11_after_with_new_section_12_retaining_title_phrase(),
        plaw_blobs={"PL 116-902": b"<lawDoc/>"},
        title=11,
        before_year="2023",
        after_year="2024",
    )

    row = {row.section_key: row for row in report.rows}["11:12"]
    assert row.row_status == "residual"
    assert row.disposition == DISPOSITION_ORACLE_SUSPECT
    assert row.rule_id == US_DRY_RUN_RESIDUAL_ORACLE_RETAINED_TITLE_SCOPE_STRIKE_RULE_ID
    assert "of this title" not in row.materialized_text
    assert "of this title" in row.oracle_text
    assert "title-strike-of-this-title@s12" in row.op_id


def _plaw_bytes_with_effective_prefix(
    title: int,
    section: str,
    enacted: str,
    struck: str,
    effective_prefix: str,
    inserted: str = "X",
) -> bytes:
    """A flat PLAW whose section chapeau carries an ``Effective ...`` prefix."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<uslm xmlns="http://schemas.gpo.gov/xml/uslm"><meta>'
        "<congress>99</congress><docNumber>4</docNumber>"
        f"<approvedDate>{enacted}</approvedDate></meta><main><section><num>1</num>"
        f"<content>{effective_prefix}<ref href=\"/us/usc/t{title}/s{section}\">Section {section} of title "
        f"{title}, United States Code</ref>, <amendingAction type=\"amend\">is amended</amendingAction>"
        ' by <amendingAction type="delete">striking</amendingAction> '
        f"“<quotedText>{struck}</quotedText>” and "
        '<amendingAction type="insert">inserting</amendingAction> '
        f"“<quotedText>{inserted}</quotedText>”.</content></section></main></uslm>"
    ).encode("utf-8")


def test_effective_date_is_parsed_from_ancestor_chapeau() -> None:
    pl = _plaw_bytes_with_effective_prefix(
        99,
        "10",
        "2024-01-01",
        "15-year",
        effective_prefix="Effective on the date that is 1 year after the date of enactment of this Act, ",
    )
    report = lower_plaw_amendatory(pl, statute_id="PL 99-4", enacted="2024-01-01")
    ops = list(report.operations())
    assert len(ops) == 1
    assert ops[0].source is not None
    assert ops[0].source.effective == "2025-01-01"


def test_future_effective_op_is_refused_not_applied_to_after_edition() -> None:
    pl = _plaw_bytes_with_effective_prefix(
        99,
        "10",
        "2024-01-01",
        "15-year",
        effective_prefix="Effective on the date that is 1 year after the date of enactment of this Act, ",
    )
    report = _build({"PL 99-4": pl})
    deferred = [
        r
        for r in report.refusals
        if r.rule_id == US_DRY_RUN_REFUSED_DEFERRED_OP_NOT_YET_EFFECTIVE_RULE_ID
    ]
    assert len(deferred) == 1
    assert deferred[0].detail["effective"] == "2025-01-01"
    # The delayed instruction is not a claim on the section for the 2024 after-edition.
    assert "99:10" not in {row.section_key for row in report.rows}


def test_immediate_absolute_effective_date_is_applied() -> None:
    # Effective date inside the after-edition window: op is not deferred.
    pl = _plaw_bytes_with_effective_prefix(
        99,
        "10",
        "2024-01-01",
        "15-year",
        effective_prefix="Effective June 15, 2024, ",
    )
    report = _build({"PL 99-5": pl})
    assert not any(
        r.rule_id == US_DRY_RUN_REFUSED_DEFERRED_OP_NOT_YET_EFFECTIVE_RULE_ID
        for r in report.refusals
    )
    rows = {row.section_key: row for row in report.rows}
    assert "99:10" in rows
    assert "the X period" in rows["99:10"].materialized_text


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
    assert row.row_status == "agree"
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
    assert row.row_status == "agree"
    assert row.rule_id == US_DRY_RUN_SECTION_AGREES_RULE_ID
    assert "19-year" in row.materialized_text
    assert "15-year" not in row.materialized_text
    # The composed row records both contributing op ids (joined by "+").
    assert "+" in row.op_id
    assert "99-3" in row.op_id and "99-4" in row.op_id


def test_absent_anchor_on_a_later_composed_op_is_refused_not_a_section_tanking_residual() -> None:
    # First op composes (15-year -> 17-year); the second strikes a phrase the running
    # text no longer carries (15-year is gone). That op's anchor is absent from the
    # running edition, so it is REFUSED (mirroring REPEAL) rather than breaking the
    # composition and tanking the whole section into an empty residual. The first op's
    # materialization survives: the section composes to "17-year". Because the oracle
    # after-text is "19-year", the surviving composition still disagrees — but it is a
    # genuine text-mismatch residual carrying the real materialized text, NOT a
    # corruption-induced empty residual, and the absent-anchor op is a visible refusal.
    pl3 = _plaw_bytes_strike_insert(
        congress=99, number=3, title=99, section="10", struck="15-year", inserted="17-year"
    )
    pl4 = _plaw_bytes_strike_insert(
        congress=99, number=4, title=99, section="10", struck="15-year", inserted="99-year"
    )
    report = _build({"PL 99-3": pl3, "PL 99-4": pl4})
    rows = {row.section_key: row for row in report.rows}
    row = rows["99:10"]
    # The surviving first-op materialization is published (not blanked out).
    assert row.row_status == "residual"
    assert row.rule_id == US_DRY_RUN_RESIDUAL_TEXT_MISMATCH_RULE_ID
    assert row.disposition == DISPOSITION_LAWVM_WRONG
    assert "17-year" in row.materialized_text
    assert row.materialized_text != ""
    # The absent-anchor op is a visible typed refusal, never silently dropped.
    refusal_rules = {r.rule_id for r in report.refusals}
    assert US_DRY_RUN_REFUSED_TEXT_TARGET_NODE_ABSENT_RULE_ID in refusal_rules


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
        action=StructuralAction.TEXT_PATCH,
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
        action=StructuralAction.TEXT_PATCH,
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


def test_heading_body_duplicate_anchor_patch_edits_body_occurrence_only() -> None:
    before = (
        '<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml"><head>'
        "<title>T23</title><!-- AUTHORITIES-USC-TITLE-ENUM:23 --></head><body><div>"
        "<!-- expcite:TITLE 23!@!CHAPTER 1!@!Sec. 109 -->"
        '<!-- field-start:head --><h3 class="section-head">&sect;109. Standards</h3>'
        "<!-- field-end:head --><!-- field-start:statute -->"
        '<p class="statutory-body">(o) Compliance With State Laws for Non-NHS '
        "Projects.&mdash;Projects shall be designed under State standards.</p>"
        "<!-- field-end:statute --></div></body></html>"
    ).encode("utf-8")
    doc = parse_usc_title_document(before, title=23, year="2020")
    section = doc.section_by_number("109")
    assert section is not None
    op = LegalOperation(
        op_id="heading-body-duplicate-anchor",
        sequence=1,
        action=StructuralAction.TEXT_PATCH,
        target=LegalAddress(path=(("title", "23"), ("section", "109"), ("subsection", "o"))),
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector(match_text="Projects", occurrence=1),
            replacement="(A) In general.—Projects",
        ),
        provenance_tags=(HEADING_BODY_DUPLICATE_ANCHOR_OCCURRENCE_PROVENANCE,),
    )

    outcome = _materialize_one(op, section.statutory_text, before_section=section)
    assert not isinstance(outcome, USDryRunRefusal)
    materialized, signal_rule_id, _disp = outcome
    assert signal_rule_id == ""
    assert "Non-NHS Projects.—(A) In general.—Projects shall be designed" in materialized
    assert "Non-NHS (A) In general" not in materialized


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
        action=StructuralAction.TEXT_PATCH,
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


def test_unlocated_subsection_last_occurrence_patch_does_not_fallback_to_section_final_period() -> None:
    # F3 §3505 shape: a terminal-punctuation edit targeting a subparagraph whose
    # node cannot be located must not rewrite the whole section's rightmost period.
    # ``occurrence=-1`` + ``occurrence_mode="Last"`` means the target node's terminal
    # punctuation, not true each-place over the section.
    op = LegalOperation(
        op_id="subparagraph-e-last-period",
        sequence=1,
        action=StructuralAction.TEXT_PATCH,
        target=LegalAddress(
            path=(
                ("title", "50"),
                ("section", "3505"),
                ("subsection", "a"),
                ("paragraph", "1"),
                ("subparagraph", "E"),
            )
        ),
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector(
                match_text=".",
                occurrence=-1,
                occurrence_mode="Last",
            ),
            replacement="; and",
        ),
    )
    before = (
        "(E) coordinate with applicable law. "
        "(F) submit a plan that shall take effect."
    )

    outcome = _materialize_one(op, before)

    assert not isinstance(outcome, USDryRunRefusal)
    materialized, signal_rule_id, disposition = outcome
    assert materialized == ""
    assert signal_rule_id == US_DRY_RUN_RESIDUAL_SUBSECTION_NODE_NOT_LOCATED_RULE_ID
    assert disposition == DISPOSITION_LAWVM_WRONG


def test_subtree_last_occurrence_patch_edits_rightmost_descendant_once() -> None:
    # If the target subtree is proven, ``Last`` can still be honored inside that
    # subtree. It edits the rightmost descendant occurrence once, not every period
    # under the paragraph and not the section-final period outside the paragraph.
    section = synthetic_usc_section(
        title=50,
        section="3505",
        text=(
            "(1) The plan shall include— "
            "(A) coordination with agencies. "
            "(B) consultation with partners. "
            "(2) The plan shall take effect."
        ),
    )
    nodes, _ = split_statutory_subsections(section)
    node_overrides: dict[tuple[tuple[str, str], ...], str] = {
        _subsection_segments(n.address): n.text for n in nodes
    }
    op = LegalOperation(
        op_id="paragraph-1-last-period",
        sequence=1,
        action=StructuralAction.TEXT_PATCH,
        target=LegalAddress(
            path=(("title", "50"), ("section", "3505"), ("paragraph", "1"))
        ),
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector(
                match_text=".",
                occurrence=-1,
                occurrence_mode="Last",
            ),
            replacement="; and",
        ),
    )

    outcome = _materialize_one(
        op,
        section.statutory_text,
        before_section=section,
        node_overrides=node_overrides,
    )

    assert not isinstance(outcome, USDryRunRefusal), outcome
    materialized, signal_rule_id, disposition = outcome
    assert signal_rule_id == ""
    assert disposition == ""
    assert "(A) coordination with agencies." in materialized
    assert "(B) consultation with partners; and" in materialized
    assert "(2) The plan shall take effect." in materialized


def test_through_tail_patch_can_span_target_heading_into_descendant() -> None:
    # PL 116-75 §2(2)(B) / 40:6121 witness: the left anchor lives in subsection
    # (b)'s heading, while the right anchor "Duties under" lives in paragraph
    # (1). The bounded through-tail op is target-subtree scoped, not just the
    # immediate node's own text and not the whole section.
    section = synthetic_usc_section(
        title=40,
        section="6121",
        text=(
            "(a) Authority.—The Marshal may act. "
            "(b) Additional Requirements.— "
            "(1) Authorization to carry firearms.—Duties under subsection "
            "shall be authorized in writing. "
            "(2) Written notice shall be retained."
        ),
    )
    nodes, _ = split_statutory_subsections(section)
    node_overrides: dict[tuple[tuple[str, str], ...], str] = {
        _subsection_segments(n.address): n.text for n in nodes
    }
    op = LegalOperation(
        op_id="through-subtree",
        sequence=1,
        action=StructuralAction.TEXT_PATCH,
        target=LegalAddress(
            path=(("title", "40"), ("section", "6121"), ("subsection", "b"))
        ),
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector(
                match_text="Additional Requirements",
                end_match_text="Duties under",
            ),
            replacement="Authorization To Carry Firearms—Duties under",
        ),
        provenance_tags=("us_amendatory", RULE_STRIKE_INSERT_THROUGH_TAIL),
    )

    outcome = _materialize_one(
        op,
        section.statutory_text,
        before_section=section,
        node_overrides=node_overrides,
    )

    assert not isinstance(outcome, USDryRunRefusal)
    materialized, signal_rule_id, disposition = outcome
    assert signal_rule_id == ""
    assert disposition == ""
    assert "Additional Requirements Related to Subsection" not in materialized
    assert "(1) Authorization to carry firearms.—" not in materialized
    assert (
        "(b) Authorization To Carry Firearms—Duties under subsection "
        "shall be authorized in writing."
    ) in materialized
    assert "(2) Written notice shall be retained." in materialized


def test_insert_after_subsection_splices_after_unlabeled_tail_descendant() -> None:
    # PL 117-58 §11513 / 23:313 witness: subsection (f)'s final legal text is an
    # unlabeled tail paragraph after paragraphs (1) and (2). "Inserting after
    # subsection (f)" must splice after that tail, not between paragraph (2) and
    # the tail.
    section = synthetic_usc_section(
        title=23,
        section="313",
        text=(
            "(f) Limitation.—If the Secretary determines that— "
            "(1) a country is a party to an agreement, and "
            "(2) the country has violated the agreement, "
            "the waiver provisions shall not apply to products "
            "produced in that foreign country. "
            "(g) Application.—The requirements apply."
        ),
    )
    nodes, _ = split_statutory_subsections(section)
    node_overrides: dict[tuple[tuple[str, str], ...], str] = {
        _subsection_segments(n.address): n.text for n in nodes
    }
    op = LegalOperation(
        op_id="insert-after-f",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("title", "23"), ("section", "313"))),
        anchor=LegalAddress(
            path=(("title", "23"), ("section", "313"), ("subsection", "f"))
        ),
        payload=IRNode(
            kind=IRNodeKind.SUBSECTION,
            label="g",
            text="(g) Waivers.—Not less than 15 days before issuing a waiver.",
        ),
    )

    outcome = _materialize_one(
        op,
        section.statutory_text,
        before_section=section,
        node_overrides=node_overrides,
    )

    assert not isinstance(outcome, USDryRunRefusal)
    materialized, signal_rule_id, disposition = outcome
    assert signal_rule_id == ""
    assert disposition == ""
    assert (
        "the waiver provisions shall not apply to products produced "
        "in that foreign country. (g) Waivers.—"
    ) in materialized


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


def test_redesignate_of_an_absent_from_node_is_refused_not_a_section_tanking_residual() -> None:
    # "redesignating paragraph (z) as (y)" where (z) is NOT present in the before
    # edition (introduced by an un-lowered sibling op, or already moved). Relabelling
    # an absent node is a no-op against the before text: a typed REFUSAL (mirroring
    # the REPEAL absent-node refusal), never a lawvm_wrong residual that would tank a
    # sibling op's correct materialization of the same section.
    section = _section77_before()
    op = LegalOperation(
        op_id="redesignate-absent-paragraph-z",
        sequence=1,
        action=StructuralAction.RENUMBER,
        target=LegalAddress(
            path=(("title", "11"), ("section", "77"), ("subsection", "b"), ("paragraph", "z"))
        ),
        destination=LegalAddress(
            path=(("title", "11"), ("section", "77"), ("subsection", "b"), ("paragraph", "y"))
        ),
    )
    outcome = _materialize_one(op, section.statutory_text, before_section=section)
    assert isinstance(outcome, USDryRunRefusal)
    assert outcome.rule_id == US_DRY_RUN_REFUSED_TEXT_TARGET_NODE_ABSENT_RULE_ID
    # The refusal embeds the offending absent enumerator (self-evidencing).
    assert "(z)" in outcome.message


def test_text_replace_against_an_absent_anchor_is_refused_not_a_section_tanking_residual() -> None:
    # A whole-section TEXT_REPLACE whose match anchor is absent from the section's
    # before edition is refused (mirroring REPEAL), not emitted as a section-tanking
    # lawvm_wrong residual. The target node the op edits is simply not present here.
    op = LegalOperation(
        op_id="text-replace-absent-anchor",
        sequence=1,
        action=StructuralAction.TEXT_PATCH,
        target=LegalAddress(path=(("title", "99"), ("section", "10"))),
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector(match_text="anchor-not-in-this-section", occurrence=0),
            replacement="whatever",
        ),
    )
    outcome = _materialize_one(op, "Section 10 body with a 15-year period.")
    assert isinstance(outcome, USDryRunRefusal)
    assert outcome.rule_id == US_DRY_RUN_REFUSED_TEXT_TARGET_NODE_ABSENT_RULE_ID
    assert "anchor-not-in-this-section" in outcome.message


def test_present_node_text_replace_still_materializes_normally() -> None:
    # Guard: the absent-target refusal must NOT swallow a PRESENT-anchor edit. A
    # TEXT_REPLACE whose anchor IS in the section still materializes (no refusal).
    op = LegalOperation(
        op_id="text-replace-present-anchor",
        sequence=1,
        action=StructuralAction.TEXT_PATCH,
        target=LegalAddress(path=(("title", "99"), ("section", "10"))),
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector(match_text="15-year", occurrence=0),
            replacement="19-year",
        ),
    )
    outcome = _materialize_one(op, "Section 10 body with a 15-year period.")
    assert not isinstance(outcome, USDryRunRefusal)
    materialized, signal_rule_id, _disp = outcome
    assert signal_rule_id == ""
    assert materialized == "Section 10 body with a 19-year period."


def test_absent_anchor_op_refusal_does_not_corrupt_a_sibling_ops_correct_materialization() -> None:
    # End-to-end de-corruption proof. Two window laws touch section 10:
    #   PL 99-5 strikes "15-year" -> "19-year"      (reaches the oracle's after text)
    #   PL 99-6 strikes "97-year" -> "98-year"      (anchor ABSENT in this window)
    # Composed in source order, PL 99-6's absent-anchor op is REFUSED (not composed),
    # so PL 99-5's correct materialization survives and the section AGREES. Before
    # this fix PL 99-6 emitted a match-text-not-found residual that broke the loop and
    # tanked the whole section into an empty lawvm_wrong residual (the corruption).
    pl5 = _plaw_bytes_strike_insert(
        congress=99, number=5, title=99, section="10", struck="15-year", inserted="19-year"
    )
    pl6 = _plaw_bytes_strike_insert(
        congress=99, number=6, title=99, section="10", struck="97-year", inserted="98-year"
    )
    report = _build({"PL 99-5": pl5, "PL 99-6": pl6})
    rows = {row.section_key: row for row in report.rows}
    assert "99:10" in rows
    row = rows["99:10"]
    # The section materializes correctly and AGREES with the oracle (de-corrupted).
    assert row.row_status == "agree", f"expected agree, got {row.row_status}/{row.rule_id}"
    assert row.rule_id == US_DRY_RUN_SECTION_AGREES_RULE_ID
    assert "19-year" in row.materialized_text
    assert "15-year" not in row.materialized_text
    # The absent-anchor op surfaces as a visible typed refusal (refuse, not repair).
    refusal_rules = {r.rule_id for r in report.refusals}
    assert US_DRY_RUN_REFUSED_TEXT_TARGET_NODE_ABSENT_RULE_ID in refusal_rules


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


def test_index_node_text_ignores_cross_reference_markers_and_indexes_true_children() -> None:
    # A replaced paragraph may cite other paragraphs with ``(1)``, ``(2)``, ``(3)``
    # markers.  Those are cross-references, not structural children.  The indexer must
    # still discover the real subparagraph children ``(A)`` and ``(B)`` that follow.
    overrides: dict[tuple[tuple[str, str], ...], str] = {}
    payload = (
        '(9) proof of claim under paragraph (1), (2), or (3) is barred, except that—'
        '“ (A) first exception; and “ (B) second exception.”'
    )
    root = _index_node_text(
        payload,
        (("subsection", "b"), ("paragraph", "9")),
        overrides,
        as_root=True,
    )
    assert root == (("subsection", "b"), ("paragraph", "9"))
    # The root keeps the FULL node text, not the truncated text before the cross-refs.
    assert overrides[root].startswith('(9) proof of claim under paragraph (1), (2), or (3)')
    assert overrides[root].endswith('second exception.”')
    # The true children are addressable for follow-on ops.
    assert overrides[
        (("subsection", "b"), ("paragraph", "9"), ("subparagraph", "A"))
    ].startswith('(A) first exception')
    assert overrides[
        (("subsection", "b"), ("paragraph", "9"), ("subparagraph", "B"))
    ].startswith('(B) second exception')


def test_repeal_of_a_node_introduced_by_an_earlier_op_is_composed() -> None:
    # A conforming strike may target a node inserted by a sibling op earlier in the
    # same section's composition (e.g. paragraph (1A) inserted and then later
    # repealed).  The REPEAL must consult the RUNNING node state, not the pristine
    # before edition, and must remove the inserted node cleanly.
    section = _section77_before()
    node_overrides: dict[tuple[tuple[str, str], ...], str] = {}
    insert_op = LegalOperation(
        op_id="insert-1A",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("title", "11"), ("section", "77"))),
        anchor=LegalAddress(
            path=(("title", "11"), ("section", "77"), ("subsection", "b"), ("paragraph", "1"))
        ),
        payload=IRNode(kind=IRNodeKind.PARAGRAPH, label="1A", text="(1A) a spliced paragraph;"),
    )
    outcome = _materialize_one(
        insert_op, section.statutory_text, before_section=section, node_overrides=node_overrides
    )
    assert not isinstance(outcome, USDryRunRefusal)
    materialized, signal_rule_id, _disp = outcome
    assert signal_rule_id == ""
    assert "(1A) a spliced paragraph" in materialized

    repeal_op = LegalOperation(
        op_id="repeal-1A",
        sequence=2,
        action=StructuralAction.REPEAL,
        target=LegalAddress(
            path=(("title", "11"), ("section", "77"), ("subsection", "b"), ("paragraph", "1A"))
        ),
    )
    outcome2 = _materialize_one(
        repeal_op,
        materialized,
        before_section=section,
        node_overrides=node_overrides,
    )
    assert not isinstance(outcome2, USDryRunRefusal)
    materialized2, signal_rule_id2, _disp2 = outcome2
    assert signal_rule_id2 == ""
    assert "(1A) a spliced paragraph" not in materialized2
    # The originally indexed paragraph (1A) is removed from live state.
    assert (("subsection", "b"), ("paragraph", "1A")) not in node_overrides


def test_insert_after_paragraph_appends_after_full_subtree() -> None:
    # A paragraph split into a parent intro plus ``(A)/(B)`` children must receive
    # an ``insert after paragraph (1)`` splice *after* child (B), not after the
    # parent intro.  This is the §541(b)(10) shape in miniature.
    section = synthetic_usc_section(
        title=11,
        section="9999",
        text=(
            "(a) intro. "
            "(b) property does not include— "
            "(1) first item but— "
            "(A) sub item alpha; and "
            "(B) sub item beta. "
            "(2) second item."
        ),
    )
    before_text = section.statutory_text
    insert_op = LegalOperation(
        op_id="insert-after-1",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("title", "11"), ("section", "9999"), ("subsection", "b"))),
        anchor=LegalAddress(
            path=(("title", "11"), ("section", "9999"), ("subsection", "b"), ("paragraph", "1"))
        ),
        payload=IRNode(kind=IRNodeKind.PARAGRAPH, label="1C", text="(1C) inserted."),
    )
    outcome = _materialize_one(
        insert_op, before_text, before_section=section, node_overrides={}
    )
    assert not isinstance(outcome, USDryRunRefusal)
    materialized, signal_rule_id, _disp = outcome
    assert signal_rule_id == ""
    assert "sub item beta. (1C) inserted." in materialized
    assert "(2) second item." in materialized
    # The wrong placement would splice between the parent intro and subparagraph (A).
    assert "but— (1C) inserted." not in materialized


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


def test_index_node_text_indexes_run_in_heads() -> None:
    # A synthetic payload can carry a run-in head like ``(B)(i) ...; and (ii)(I)``,
    # where the child marker follows the parent's closing parenthesis with no space.
    # The indexer must still place ``(i)`` and ``(I)`` as descendants so later ops
    # can target them.  This is the §101(10A)(B) CARES-act shape in miniature.
    overrides: dict[tuple[tuple[str, str], ...], str] = {}
    payload = (
        '(B)(i) first-included item; and '
        '"(ii) excluded items—" '
        '"(I) first exclusion; and "(II) second exclusion."'
    )
    root = _index_node_text(
        payload,
        (("paragraph", "10A"), ("subparagraph", "B")),
        overrides,
        as_root=True,
    )
    assert root == (("paragraph", "10A"), ("subparagraph", "B"))
    assert overrides[root].startswith("(B)(i) first-included item")
    assert overrides[
        (("paragraph", "10A"), ("subparagraph", "B"), ("clause", "i"))
    ].startswith("(i) first-included item")
    assert overrides[
        (("paragraph", "10A"), ("subparagraph", "B"), ("clause", "ii"))
    ].startswith("(ii) excluded items")
    assert overrides[
        (
            ("paragraph", "10A"),
            ("subparagraph", "B"),
            ("clause", "ii"),
            ("subclause", "I"),
        )
    ].startswith("(I) first exclusion")
    assert overrides[
        (
            ("paragraph", "10A"),
            ("subparagraph", "B"),
            ("clause", "ii"),
            ("subclause", "II"),
        )
    ].startswith("(II) second exclusion")


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


def test_norm_editorial_undoes_olrc_dash_word_courtesy_space() -> None:
    # 40:6121 witness: source-faithful through-tail materialization has
    # ``Firearms—Duties``; OLRC inserts a courtesy space after the em dash.
    faithful = "Authorization To Carry Firearms—Duties under subsection"
    published = "Authorization To Carry Firearms— Duties under subsection"
    other = "Authorization To Carry Firearms—Other duties under subsection"

    assert _norm_editorial(faithful) == _norm_editorial(published)
    assert _norm_editorial(faithful) != _norm_editorial(other)


def test_norm_editorial_undoes_quoted_block_marker_spacing() -> None:
    # AIA chapter 32: USLM quotedContent wraps inserted subsection/list markers.
    # After quote stripping, faithful materialization has punctuation immediately
    # followed by the marker; OLRC inserts courtesy spaces on the Code surface.
    from lawvm.core.comparison_normalization import normalize_inline_comparison_text

    enacted = "review.“(b) Scope.—Fees;“(2) parties in interest."
    published = "review. (b) Scope.—Fees; (2) parties in interest."
    assert normalize_inline_comparison_text(enacted) != normalize_inline_comparison_text(
        published
    )
    assert _norm_editorial(enacted) == _norm_editorial(published)
    # The projection only erases punctuation-to-marker spacing, not content.
    other = "review. (c) Scope.—Fees; (2) parties in interest."
    assert _norm_editorial(enacted) != _norm_editorial(other)


def test_norm_editorial_undoes_semicolon_and_courtesy_space() -> None:
    # AIA §25: inserting quotedText "and" after a semicolon faithfully produces
    # ``;and``; OLRC prints the conjunction with courtesy spacing.
    from lawvm.core.comparison_normalization import normalize_inline_comparison_text

    enacted = "consistent with impartiality;and (G) may prioritize applications"
    published = "consistent with impartiality; and (G) may prioritize applications"
    assert normalize_inline_comparison_text(enacted) != normalize_inline_comparison_text(
        published
    )
    assert _norm_editorial(enacted) == _norm_editorial(published)
    # It is not a blanket semicolon-space eraser.
    other = "consistent with impartiality; or (G) may prioritize applications"
    assert _norm_editorial(enacted) != _norm_editorial(other)


def test_norm_editorial_undoes_conjunction_marker_courtesy_space() -> None:
    # AIA chapter 32 also carries quoted list continuations where OLRC spaces the
    # marker after the conjunction: ``;and(B)`` vs ``; and (B)``.
    from lawvm.core.comparison_normalization import normalize_inline_comparison_text

    enacted = "the petition;and“(B) affidavits; or“(2) no response is filed"
    published = "the petition; and (B) affidavits; or (2) no response is filed"
    assert normalize_inline_comparison_text(enacted) != normalize_inline_comparison_text(
        published
    )
    assert _norm_editorial(enacted) == _norm_editorial(published)
    other = "the petition; and (C) affidavits; or (2) no response is filed"
    assert _norm_editorial(enacted) != _norm_editorial(other)


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


def test_norm_editorial_undoes_hyphenated_compound_line_wrap_space() -> None:
    # F1 case: the enacted USLM quotedText payload preserved a line-wrap space after
    # an intra-word hyphen (``the non- Federal cost share`` — the PL broke
    # ``non-Federal`` across a line); the OLRC consolidated Code re-joins the
    # hyphenated compound (``non-Federal``). Real conviction: title23:2020->2022 §176.
    from lawvm.core.comparison_normalization import normalize_inline_comparison_text

    faithful = "to meet the non- Federal cost share requirement for a project"
    published = "to meet the non-Federal cost share requirement for a project"
    # Plain inline normalization keeps them apart; the editorial projection unifies.
    assert normalize_inline_comparison_text(faithful) != normalize_inline_comparison_text(
        published
    )
    assert _norm_editorial(faithful) == _norm_editorial(published)
    # The fold requires a hyphen with a word char on BOTH sides (``\w-\s+\w``). A
    # space after a standalone dash (no leading word char) is NOT the hyphen-wrap
    # pattern, so a genuine ``word - word`` spaced dash survives distinct from a
    # concatenated ``word-word`` compound (the fold never masks that difference).
    spaced_dash = "the cost - share requirement"
    joined = "the cost-share requirement"
    assert _norm_editorial(spaced_dash) != _norm_editorial(joined)
    # And it never masks a genuine content divergence.
    other = "to meet the non- State cost share requirement for a project"
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


def test_source_truncated_clause_payload_is_detected() -> None:
    # Mirrors PL 116-54 /s4/a/1/B/ii/II: the USLM quotedContent introduces clause (i)
    # with a bare noun phrase, while the oracle shows the completed body.
    materialized = (
        "(51D) The term small business debtor—"
        "(A) means a person; and"
        "(B) does not include—(i) any member (ii) any debtor that is a corporation."
    )
    oracle = (
        "(51D) The term small business debtor—"
        "(A) means a person; and"
        "(B) does not include—"
        "(i) any member of a group of affiliated debtors that has aggregate debts; "
        "(ii) any debtor that is a corporation."
    )
    assert _has_source_truncated_clause_payload(materialized, oracle) is True
    # A clause whose materialized body already ends with a terminal marker is not
    # treated as truncated: the source considered it complete.
    complete = (
        "(B) does not include—(i) any member; (ii) any debtor that is a corporation."
    )
    assert _has_source_truncated_clause_payload(complete, oracle) is False
    # Requiring the oracle body to continue substantially rules out false positives
    # where only punctuation differs.
    short_oracle = (
        "(B) does not include—(i) any member (ii) any debtor."
    )
    assert _has_source_truncated_clause_payload(complete, short_oracle) is False


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
        enacted = ""

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
    assert row.row_status == "residual"
    assert row.disposition == DISPOSITION_ORACLE_SUSPECT
    assert row.rule_id == US_DRY_RUN_RESIDUAL_TEXT_MISMATCH_RULE_ID
    # The leading USLM wrapper quote is stripped (serialization artifact, not
    # enacted statutory text); the internal paragraph-delimiter quote remains,
    # so the residual is still editorial (F1), not repaired to the oracle.
    assert "“(2) second" in row.materialized_text
    assert "“(1)" not in row.materialized_text


def test_olrc_grammar_cleanup_after_clause_deletion_is_oracle_suspect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AIA §256 family: source-faithful deletion can leave stale grammar.

    The enacted op deletes an intervening clause and leaves the retained before
    text's "issued a certificate" untouched. The OLRC consolidation renders
    "issue a certificate". Dry-run must keep the source-faithful materialization
    and type the difference as oracle/editorial, not mutate the retained verb.
    """

    before_text = (
        "Whenever through error an inventor is not named in an issued patent "
        "and such error arose without any deceptive intention on his part, "
        "the Director may, on application, issued a certificate correcting such error."
    )
    oracle_text = (
        "Whenever through error an inventor is not named in an issued patent, "
        "the Director may, on application, issue a certificate correcting such error."
    )
    wrong_oracle_text = oracle_text.replace("application", "verified application")

    def _htm(text: str) -> bytes:
        return (
            '<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml"><head>'
            "<title>T99</title><!-- AUTHORITIES-USC-TITLE-ENUM:99 --></head><body><div>"
            "<!-- expcite:TITLE 99!@!CHAPTER 1!@!Sec. 256 -->"
            '<!-- field-start:head --><h3 class="section-head">&sect;256. Correction</h3>'
            "<!-- field-end:head --><!-- field-start:statute -->"
            f'<p class="statutory-body">{text}</p>'
            "<!-- field-end:statute --></div></body></html>"
        ).encode("utf-8")

    op = LegalOperation(
        op_id="PL 99-256#delete-clause",
        sequence=1,
        action=StructuralAction.TEXT_PATCH,
        target=LegalAddress(path=(("title", "99"), ("section", "256"))),
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.DELETE,
            selector=TextSelector(
                match_text="and such error arose without any deceptive intention on his part",
                occurrence=1,
            ),
            replacement=None,
        ),
        source=OperationSource(statute_id="PL 99-256", enacted="2024-01-01"),
    )

    class _Report:
        enacted = "2024-01-01"
        instructions = ()

        def operations(self) -> tuple[LegalOperation, ...]:
            return (op,)

    monkeypatch.setattr(
        "lawvm.us_federal.dry_run.lower_plaw_amendatory",
        lambda *a, **k: _Report(),
    )

    report = build_us_dry_run(
        before_htm=_htm(before_text),
        after_htm=_htm(oracle_text),
        plaw_blobs={"PL 99-256": b"<uslm/>"},
        title=99,
        before_year="2023",
        after_year="2024",
    )
    row = report.rows[0]
    assert row.section_key == "99:256"
    assert row.rule_id == US_DRY_RUN_RESIDUAL_OLRC_GRAMMAR_CLEANUP_RULE_ID
    assert row.disposition == DISPOSITION_ORACLE_SUSPECT
    assert "issued a certificate correcting such error" in row.materialized_text
    assert "issue a certificate correcting such error" in row.oracle_text

    negative = build_us_dry_run(
        before_htm=_htm(before_text),
        after_htm=_htm(wrong_oracle_text),
        plaw_blobs={"PL 99-256": b"<uslm/>"},
        title=99,
        before_year="2023",
        after_year="2024",
    )
    negative_row = negative.rows[0]
    assert negative_row.rule_id == US_DRY_RUN_RESIDUAL_TEXT_MISMATCH_RULE_ID
    assert negative_row.disposition == DISPOSITION_LAWVM_WRONG


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


# ---------------------------------------------------------------------------
# INSERT catchline-mismatch refusal (§1.1: no silent target hijacking)
# ---------------------------------------------------------------------------

# Real witness: PL 110-246 SEC. 416 ("ANNUAL REPORT.") creates USC 7:228d. The
# amendatory lowerer dispatched the INSERT op with the Verbatim PL section
# heading ``SEC. 416.`` as the leading catchline of the payload, but routed the
# op target onto the *existing* USC 7:228d. The pre-existing dry-run appended
# the new section's body to the existing section's body, producing a 5752-char
# materialization against a 376-char oracle — a 15.3x over-insert ``lawvm_wrong``
# row. The same audit class is present on PL 110-246 SEC. 209 -> 7:7511 (over-
# insert 10.7x). 123 such mis-routed INSERT ops were identified across the
# 14 high-activity windows in the post-classification-index bench.


def _insert_catchline_mismatch_op(
    *,
    target_section: str = "228d",
    payload_text: str = (
        "SEC. 416. ANNUAL REPORT."
        "“(a) In General.—Not later than March 1 of each year, the "
        "Secretary shall submit to Congress and make publicly available "
        "a report that—“(1) states, for the preceding year, separately for "
        "livestock and poultry and separately by enforcement area category."
    ),
) -> LegalOperation:
    return LegalOperation(
        op_id="PL 110-246#instr640",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(
            path=(("title", "7"), ("section", target_section))
        ),
        payload=IRNode(
            kind=IRNodeKind.SECTION, label=target_section, text=payload_text
        ),
    )


def test_insert_with_statutes_at_large_catchline_for_a_different_section_is_refused() -> None:
    """§1.1 guard-liveness: a whole-section INSERT whose payload opens with a
    different section's catchline (here Statutes-at-Large ``SEC. 416.`` for
    an op targeting ``7:228d``) is REFUSED — never composed as a wrong
    materialization that appends another section's body to this target.

    Real witness: PL 110-246 SEC. 416 creates USC 7:228d (a NEW section); the
    amendatory lowerer mis-routed the op to the existing target ``title:7/
    section:228d``. Faithfully appending the body would produce a 15x over-
    insert over the unchanged 376-char oracle (the section is unchanged
    in the oracle: PL 110-246 SEC. 416 is a creation elsewhere).

    AGENTS.md §0/§1.1: a mis-routed op is preserved as a typed refusal, never
    silently composed. The refusal embeds the offending payload preview so
    a reviewer can triage without re-running extraction (§1.10).
    """
    from lawvm.us_federal.dry_run import (
        US_DRY_RUN_REFUSED_INSERT_CATCHLINE_MISMATCH_RULE_ID,
    )

    op = _insert_catchline_mismatch_op()
    before_text = (
        "Not later than March 1 of each year, the Secretary shall submit to "
        "Congress and make publicly available a report that— (1) assesses the "
        "general economic state of the cattle and hog industries."
    )
    outcome = _materialize_one(op, before_text)
    assert isinstance(outcome, USDryRunRefusal)
    assert outcome.rule_id == US_DRY_RUN_REFUSED_INSERT_CATCHLINE_MISMATCH_RULE_ID
    assert outcome.target_address == "title:7/section:228d"
    # The diagnostic embeds the offending payload preview (§1.10) — the
    # leading catchline_carrier must be visible without re-running extraction.
    assert "SEC. 416." in outcome.detail["payload_preview"]
    assert outcome.detail["target_section"] == "228d"
    assert outcome.detail["payload_catchline_section"] == "416"
    # The message distinguishes the catchline-section mismatch from any other
    # refusal class (no opaque "missing source" string; §1.10 says the
    # diagnostic must name the concrete fix path).
    assert "INSERT op targets section '228d'" in outcome.message
    assert "catchline for '416'" in outcome.message


def test_insert_with_uslm_catchline_for_a_different_section_is_refused() -> None:
    """Negative-side coverage for the USLM positive-law form ``§ <num>.`` of the
    same family. Real witness: PL 116-283 SEC. 1807 routed ``§ 3066.`` (USC
    5:3066) onto target ``10:2311``. Both catchline forms (USLM ``§`` and
    Statutes-at-Large ``SEC.``) route through the same detector.
    """
    from lawvm.us_federal.dry_run import (
        US_DRY_RUN_REFUSED_INSERT_CATCHLINE_MISMATCH_RULE_ID,
    )

    op = _insert_catchline_mismatch_op(
        target_section="2311",
        payload_text=(
            "§ 3066. Assignment and delegation of procurement functions and "
            "responsibilities: procurements for or with other agencies"
            "“(a) In General.—The head of an executive agency may delegate..."
        ),
    )
    outcome = _materialize_one(op, "(a) old body for section 2311.")
    assert isinstance(outcome, USDryRunRefusal)
    assert outcome.rule_id == US_DRY_RUN_REFUSED_INSERT_CATCHLINE_MISMATCH_RULE_ID
    assert outcome.detail["target_section"] == "2311"
    assert outcome.detail["payload_catchline_section"] == "3066"
    # The payload preview is the leading ~400 chars — covers the catchline and
    # part of the heading.
    assert "§ 3066." in outcome.detail["payload_preview"]


def test_insert_with_catchline_matching_target_section_is_not_refused() -> None:
    """Negative test: a legitimate whole-new-section insert whose payload
    catchline NAMES the target section is NOT refused. The op composes
    normally (catchline is projected off the body-only oracle surface).

    Guards against an over-broad refusal that would convert whole-new-section
    creation into a false-positive target-hijacking finding.
    """
    from lawvm.us_federal.dry_run import (
        US_DRY_RUN_REFUSED_INSERT_CATCHLINE_MISMATCH_RULE_ID,
    )

    op = _insert_catchline_mismatch_op(
        target_section="228d",
        payload_text=(
            "§ 228d. Annual report."
            "“(a) In General.—Not later than March 1 of each year..."
        ),
    )
    outcome = _materialize_one(op, "(a) old body.")
    assert not isinstance(outcome, USDryRunRefusal)
    materialized, signal_rule_id, _disp = outcome
    assert signal_rule_id == ""
    assert US_DRY_RUN_REFUSED_INSERT_CATCHLINE_MISMATCH_RULE_ID not in (
        materialized  # the rule_id cannot ride into the materialized text
    )
    # The matching catchline is projected off; the body survives under its own
    # subsection markers (per the existing strip_replacement_section_catchline
    # contract — confirmed by test_whole_section_replace_projects_off_its_own_catchline).
    assert "§ 228d." not in materialized
    assert "(a) In General." in materialized


def test_insert_body_only_payload_is_not_refused_by_catchline_check() -> None:
    """A body-only insert payload (no leading ``§`` or ``SEC.`` catchline) is
    NOT refused by the catchline-mismatch check. Body-only insert ops route
    through the pre-existing append path (the most common sub-section insert).
    """
    from lawvm.us_federal.dry_run import US_DRY_RUN_REFUSED_INSERT_CATCHLINE_MISMATCH_RULE_ID

    op = _insert_catchline_mismatch_op(
        target_section="228d",
        payload_text="“(a) A new subsection inserted at the end of section 228d.",
    )
    outcome = _materialize_one(op, "Existing body text.")
    assert not isinstance(outcome, USDryRunRefusal)
    materialized, signal_rule_id, _disp = outcome
    assert signal_rule_id == ""
    assert US_DRY_RUN_REFUSED_INSERT_CATCHLINE_MISMATCH_RULE_ID != signal_rule_id
    # Leading USLM wrapper quote is stripped (existing behavior).
    assert "Existing body text." in materialized
    assert "“" not in materialized


def _insert_catchline_mismatch_style_op_goes_through_full_dry_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drives the refusal through the FULL composition path (guard-liveness
    per AGENTS.md §2.9): the catchline-mismatch refusal fires when the op is
    lowered by amendatory and routed through build_us_dry_run, not just when
    _materialize_one is called directly.
    """
    from lawvm.us_federal.dry_run import US_DRY_RUN_REFUSED_INSERT_CATCHLINE_MISMATCH_RULE_ID

    op = _insert_catchline_mismatch_op()
    before = (
        '<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml"><head>'
        "<title>T7</title><!-- AUTHORITIES-USC-TITLE-ENUM:7 --></head><body><div>"
        "<!-- expcite:TITLE 7!@!CHAPTER 64!@!Sec. 228d -->"
        '<!-- field-start:head --><h3 class="section-head">&sect;228d. Existing body</h3>'
        "<!-- field-end:head --><!-- field-start:statute -->"
        '<p class="statutory-body">This is the existing body of section 228d.</p>'
        "<!-- field-end:statute --></div></body></html>"
    ).encode("utf-8")
    after = before  # oracle unchanged (the mis-routed insert never touched this section)

    class _OneOpReport:
        enacted = ""

        def operations(self) -> list[LegalOperation]:
            return [op]

    # Monkeypatch lower_plaw_amendatory so build_us_dry_run sees ONLY the
    # catchline-mismatch op. This drives the op through the full Phase 1 + Phase 2
    # composition path (the path real amendatory ops take), not just a unit test
    # of _materialize_one.
    monkeypatch.setattr(
        "lawvm.us_federal.dry_run.lower_plaw_amendatory",
        lambda *a, **k: _OneOpReport(),
    )
    report = build_us_dry_run(
        before_htm=before,
        after_htm=after,
        plaw_blobs={"PL 110-246": b"<uslm/>"},
        title=7,
        before_year="2006",
        after_year="2008",
    )

    # The mismatched INSERT must surface as a typed refusal, not as a row with
    # an inflated materialized_text (the §0/§1.1 contract).
    matching_refusals = [
        f for f in report.refusals
        if f.rule_id == US_DRY_RUN_REFUSED_INSERT_CATCHLINE_MISMATCH_RULE_ID
    ]
    assert matching_refusals, (
        "catchline-mismatch INSERT must surface as a typed refusal through "
        "the full build_us_dry_run path, not just _materialize_one"
    )
    assert len(matching_refusals) == 1
    refusal = matching_refusals[0]
    assert refusal.target_address == "title:7/section:228d"
    assert refusal.detail["target_section"] == "228d"
    assert refusal.detail["payload_catchline_section"] == "416"
    # No row is produced (the section's only op was refused; before-text
    # matched oracle anyway — but the row is suppressed because op_ids is
    # empty). This is the over-retention safe path: not a wrong materialization.
    rows_for_section = [
        r for r in report.rows if r.section_key == "7:228d"
    ]
    assert rows_for_section == []


def test_insert_catchline_mismatch_drives_full_dry_run_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wrapper that runs the full-path guard-liveness test as a real test
    function (so the class-style helper doesn't get collected as a test).
    """
    _insert_catchline_mismatch_style_op_goes_through_full_dry_run(monkeypatch)


# ---------------------------------------------------------------------------
# Composite ops on ONE sub-section node: running-node threading + dual-identical
# patch handling (the §130i canary). Two ops against the same node must compose on
# the running node text, and two SAME-anchor patches must each consume their own
# occurrence left-to-right — never collapse onto one occurrence.
# ---------------------------------------------------------------------------


def _section79_dual_anchor_htm() -> bytes:
    # §79: paragraph (a)(1) carries the anchor phrase "sections 4173(i)" TWICE, while
    # subsection (a)'s own line carries it ZERO times (the node scope must confine the
    # edit). Used to prove two SAME-anchor patches on one node consume their own
    # (distinct) occurrences in source order, and that a same-anchor patch with no
    # remaining occurrence is refused (the §130i single-occurrence shape).
    return (
        '<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml"><head>'
        "<title>T10</title><!-- AUTHORITIES-USC-TITLE-ENUM:10 --></head><body><div>"
        "<!-- expcite:TITLE 10!@!CHAPTER 1!@!Sec. 79 -->"
        '<!-- field-start:head --><h3 class="section-head">&sect;79. Dual anchor demo</h3>'
        "<!-- field-end:head --><!-- field-start:statute -->"
        '<p class="statutory-body">(a) The agency shall report as follows—</p>'
        '<p class="statutory-body-1em">(1) a base (as defined in sections 4173(i) of '
        "this title) and another base (as defined in sections 4173(i) of this title).</p>"
        "<!-- field-end:statute --></div></body></html>"
    ).encode("utf-8")


_DUAL_ANCHOR_SEGMENTS = (("subsection", "a"), ("paragraph", "1"))


def _dual_anchor_op(op_id: str, sequence: int) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.TEXT_PATCH,
        target=LegalAddress(
            path=(
                ("title", "10"),
                ("section", "79"),
                ("subsection", "a"),
                ("paragraph", "1"),
            )
        ),
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector(match_text="sections 4173(i)", occurrence=0),
            replacement="section 4173",
        ),
    )


def test_two_identical_patches_on_one_node_consume_distinct_occurrences_left_to_right() -> None:
    # The dual-identical-patch case: TWO ops with the SAME match_text
    # ("sections 4173(i)" -> "section 4173") target the SAME node, which carries the
    # anchor TWICE. Threaded on the running node text, patch 0 rewrites the LEFTMOST
    # occurrence and records the result; patch 1 then operates on the post-patch node
    # and rewrites the SECOND occurrence. They must NOT collapse onto one occurrence.
    doc = parse_usc_title_document(_section79_dual_anchor_htm(), title=10, year="2023")
    section = doc.section_by_number("79")
    assert section is not None
    before_text = section.statutory_text
    assert before_text.count("sections 4173(i)") == 2

    node_overrides: dict[tuple[tuple[str, str], ...], str] = {}
    running = before_text
    for op_id, seq in (("dual-0", 1), ("dual-1", 2)):
        op = _dual_anchor_op(op_id, seq)
        outcome = _materialize_one(
            op, running, before_section=section, node_overrides=node_overrides
        )
        assert not isinstance(outcome, USDryRunRefusal), outcome
        running, signal_rule_id, _disp = outcome
        assert signal_rule_id == ""

    # Both occurrences are consumed; neither survives, and no third edit happened.
    assert running.count("sections 4173(i)") == 0
    assert running.count("section 4173") == 2
    assert "(a) The agency shall report as follows" in running


def test_third_identical_patch_with_no_remaining_occurrence_is_refused_not_collapsed() -> None:
    # §130i canary shape: the node carries the anchor TWICE but THREE identical
    # same-anchor patches target it. The first two consume the two occurrences; the
    # THIRD finds no remaining anchor in the running node and is REFUSED as an absent
    # anchor — never collapsed onto a prior patch's edit, never a wrong materialization.
    doc = parse_usc_title_document(_section79_dual_anchor_htm(), title=10, year="2023")
    section = doc.section_by_number("79")
    assert section is not None
    before_text = section.statutory_text

    node_overrides: dict[tuple[tuple[str, str], ...], str] = {}
    running = before_text
    outcomes = []
    for op_id, seq in (("dual-0", 1), ("dual-1", 2), ("dual-2", 3)):
        op = _dual_anchor_op(op_id, seq)
        outcome = _materialize_one(
            op, running, before_section=section, node_overrides=node_overrides
        )
        outcomes.append(outcome)
        if isinstance(outcome, USDryRunRefusal):
            continue
        running, _rule, _disp = outcome

    # First two composed; the third is a typed absent-anchor refusal (no collapse).
    assert not isinstance(outcomes[0], USDryRunRefusal)
    assert not isinstance(outcomes[1], USDryRunRefusal)
    assert isinstance(outcomes[2], USDryRunRefusal)
    assert outcomes[2].rule_id == US_DRY_RUN_REFUSED_TEXT_TARGET_NODE_ABSENT_RULE_ID
    # The two genuine occurrences are still both consumed; the refusal added nothing.
    assert running.count("section 4173") == 2
    assert running.count("sections 4173(i)") == 0


def test_single_occurrence_node_with_two_identical_patches_keeps_one_edit_and_refuses_the_rest() -> None:
    # The exact §130i mechanism in miniature: a prior op rewrote the node so only ONE
    # occurrence of the dual anchor exists when the identical patches run. Patch 0
    # consumes it; patch 1 (identical) finds the anchor gone in the running node and
    # is refused — NOT a lawvm_wrong residual that would tank the section. The single
    # edit survives and the section can still compose to an agreement downstream.
    doc = parse_usc_title_document(_section79_dual_anchor_htm(), title=10, year="2023")
    section = doc.section_by_number("79")
    assert section is not None
    node_overrides: dict[tuple[tuple[str, str], ...], str] = {}

    # Pre-rewrite the node so only ONE "sections 4173(i)" remains (simulating an
    # earlier sibling patch). We do this by applying a single each-place-style strike
    # of the SECOND occurrence via a direct running-text edit, then run the patches.
    running = section.statutory_text.replace("sections 4173(i)", "section 4173", 1)
    assert running.count("sections 4173(i)") == 1

    # Seed the override to the post-edit node text so the threading sees the live node.
    located = _running_node_text(
        section,
        _dual_anchor_op("seed", 0).target,
        section.statutory_text,
        None,
    )
    assert located is not None
    node_overrides[_DUAL_ANCHOR_SEGMENTS] = located.replace(
        "sections 4173(i)", "section 4173", 1
    )

    op0 = _dual_anchor_op("p0", 1)
    out0 = _materialize_one(op0, running, before_section=section, node_overrides=node_overrides)
    assert not isinstance(out0, USDryRunRefusal)
    running, rule0, _d0 = out0
    assert rule0 == ""
    assert running.count("sections 4173(i)") == 0
    assert running.count("section 4173") == 2

    op1 = _dual_anchor_op("p1", 2)
    out1 = _materialize_one(op1, running, before_section=section, node_overrides=node_overrides)
    # The identical second patch finds no anchor in the running node: refused, not a
    # section-tanking residual, not a collapse onto patch 0's edit.
    assert isinstance(out1, USDryRunRefusal)
    assert out1.rule_id == US_DRY_RUN_REFUSED_TEXT_TARGET_NODE_ABSENT_RULE_ID


def test_basic_composite_two_ops_on_one_node_compose_on_running_node_text() -> None:
    # A plain composite case: two DIFFERENT patches on the same node. The second must
    # act on the text the first produced (running-node threading), not the pristine
    # before node. "15-year" -> "16-year" then "16-year" -> "17-year" must reach
    # "17-year"; without threading the second patch's "16-year" anchor would be absent
    # from the pristine node and the op would wrongly refuse.
    section = _section77_before()
    node_overrides: dict[tuple[tuple[str, str], ...], str] = {}
    target = LegalAddress(
        path=(("title", "11"), ("section", "77"), ("subsection", "b"), ("paragraph", "1"))
    )

    def _patch_op(op_id: str, seq: int, struck: str, inserted: str) -> LegalOperation:
        return LegalOperation(
            op_id=op_id,
            sequence=seq,
            action=StructuralAction.TEXT_PATCH,
            target=target,
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text=struck, occurrence=0),
                replacement=inserted,
            ),
        )

    running = section.statutory_text
    out0 = _materialize_one(
        _patch_op("c0", 1, "15-year", "16-year"),
        running,
        before_section=section,
        node_overrides=node_overrides,
    )
    assert not isinstance(out0, USDryRunRefusal)
    running, _r0, _d0 = out0
    out1 = _materialize_one(
        _patch_op("c1", 2, "16-year", "17-year"),
        running,
        before_section=section,
        node_overrides=node_overrides,
    )
    assert not isinstance(out1, USDryRunRefusal), out1
    running, rule1, _d1 = out1
    assert rule1 == ""
    # The second patch composed on the running node: (b)(1) is now "17-year".
    assert "the first paragraph mentions a 17-year window" in running
    # Subsection (a)'s identical "15-year" was never in the (b)(1) node, so untouched.
    assert "first subsection mentions a 15-year" in running


def test_container_level_token_replace_applies_to_descendants_when_anchor_lives_deeper() -> None:
    # When the source amends a container ("in paragraph (1) ...") and the token to
    # replace only appears inside deeper subparagraphs/clauses, the materializer
    # must still find and replace it without mis-applying the patch to a sibling
    # container.  Paragraph (1)'s own node text ends at the colon; ``120`` lives in
    # subparagraphs (A) and (B).  Replacing ``120`` each place under paragraph (1)
    # must hit both occurrences and leave an unrelated ``120`` in paragraph (2)
    # untouched.
    section = synthetic_usc_section(
        title=11,
        section="365",
        text=(
            "(1) The period is the earlier of— "
            "(A) 120 days; or "
            "(B) 120 days after notice. "
            "(2) A separate 120-day period applies for other purposes."
        ),
    )
    nodes, _ = split_statutory_subsections(section)
    node_overrides: dict[tuple[tuple[str, str], ...], str] = {
        _subsection_segments(n.address): n.text for n in nodes
    }
    target = LegalAddress(
        path=(
            ("title", "11"),
            ("section", "365"),
            ("paragraph", "1"),
        )
    )
    op = LegalOperation(
        op_id="strike-120-each-place-in-para-1",
        sequence=1,
        action=StructuralAction.TEXT_PATCH,
        target=target,
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector(match_text="120", occurrence=-1),
            replacement="210",
        ),
    )
    outcome = _materialize_one(
        op, section.statutory_text, before_section=section, node_overrides=node_overrides
    )
    assert not isinstance(outcome, USDryRunRefusal), outcome
    materialized, rule, disposition = outcome
    assert rule == ""
    assert disposition == ""
    # Both ``120`` tokens under paragraph (1) became ``210``.
    para1_prefix = "(1) The period is the earlier of—"
    para1_span = materialized[materialized.find(para1_prefix) :]
    para1_part = para1_span[: para1_span.find("(2)")]
    assert para1_part.count("210") == 2
    assert para1_part.count("120") == 0
    # Paragraph (2)'s ``120-day`` token stayed untouched.
    assert "(2) A separate 120-day period applies for other purposes." in materialized
    # The override for the deeper descendants (not just the target paragraph) was
    # updated so later ops see the running state.
    assert any(
        "210" in text
        for path, text in node_overrides.items()
        if ("paragraph", "1") in path
    )


def test_tail_strike_each_place_cuts_at_the_leftmost_anchor_only() -> None:
    """A tail-to-end strike is single-cut: "each place" cannot multiply it.

    "striking 'X' and all that follows and inserting 'Y'" deletes everything from
    the anchor to the end of the node. The leftmost anchor's cut already removes
    every later anchor, so an "each place" tail strike (``count == -1``) is
    identical to a first-occurrence one (``count == 1``): both cut at the leftmost
    anchor and append the replacement exactly once. The replacement must survive
    (the old clobbering loop kept only the leftmost cut but is pinned here so it
    can never silently regress to dropping the inserted text).
    """
    text = "alpha unless X beta unless Y gamma unless Z end"
    each_place = _replace_token_tail_in_text(text, "unless", "; provided.", count=-1)
    first_only = _replace_token_tail_in_text(text, "unless", "; provided.", count=1)
    assert each_place == first_only == "alpha ; provided."
    # The inserted replacement is present exactly once, and the tail is gone.
    assert each_place.count("; provided.") == 1
    assert "beta" not in each_place and "gamma" not in each_place

    # An empty replacement is a pure tail deletion at the leftmost anchor.
    assert _replace_token_tail_in_text(text, "unless", "", count=-1) == "alpha "

    # An anchor absent from the text is a no-op (never an over-broad deletion).
    assert _replace_token_tail_in_text(text, "absent", "Y", count=-1) == text


# A section whose visible markers skip an entire level the amendment names. The
# source tree exposes only subparagraph ``(A)/(B)`` units while the amendment
# targets ``paragraph (1)``: the paragraph level is absent, so the failure is a
# source-footing gap, not a lawvm_wrong lowering bug.
_TARGET_LEVEL_ABSENT_SECTION = synthetic_usc_section(
    title=42,
    section="1395w-demo",
    text="(A) The first visible unit. (B) The second visible unit.",
)


def test_subsection_replace_when_target_level_absent_is_source_footing_gap() -> None:
    op = LegalOperation(
        op_id="replace-para-1",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(
            path=(
                ("title", "42"),
                ("section", "1395w-demo"),
                ("paragraph", "1"),
            )
        ),
        payload=IRNode(
            kind=IRNodeKind.SUBSECTION,
            label="1",
            text="(1) Replacement paragraph.",
        ),
    )
    outcome = _materialize_one(
        op,
        _TARGET_LEVEL_ABSENT_SECTION.statutory_text,
        before_section=_TARGET_LEVEL_ABSENT_SECTION,
    )
    assert not isinstance(outcome, USDryRunRefusal)
    _materialized, rule_id, disposition = outcome
    assert rule_id == US_DRY_RUN_RESIDUAL_TARGET_LEVEL_ABSENT_IN_SOURCE_TREE_RULE_ID
    assert disposition == DISPOSITION_MISSING_SOURCE


def test_target_level_absent_not_fired_when_level_is_present_but_label_missing() -> None:
    # Section has paragraph (1) but not paragraph (2). The level exists, so a
    # missing target label stays the generic node-not-located residual.
    section = synthetic_usc_section(
        title=11,
        section="77",
        text="(1) The first paragraph. (2) The second paragraph.",
    )
    op = LegalOperation(
        op_id="replace-para-99",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(
            path=(("title", "11"), ("section", "77"), ("paragraph", "99"))
        ),
        payload=IRNode(
            kind=IRNodeKind.SUBSECTION,
            label="99",
            text="(99) Replacement paragraph.",
        ),
    )
    outcome = _materialize_one(
        op, section.statutory_text, before_section=section
    )
    assert not isinstance(outcome, USDryRunRefusal)
    _materialized, rule_id, disposition = outcome
    assert rule_id == US_DRY_RUN_RESIDUAL_SUBSECTION_NODE_NOT_LOCATED_RULE_ID
    assert disposition == DISPOSITION_LAWVM_WRONG


def test_target_ancestor_absent_when_deeper_level_exists_but_parent_missing() -> None:
    """A section may expose clause-level markers but not the subsection/paragraph
    ancestors that own them. The target level (clause) exists, so the old
    `target_level_absent` rule does not fit; the new `target_ancestor_absent` rule
    owns the gap.
    """
    section = synthetic_usc_section(
        title=47,
        section="227",
        text="(A) The term. (i) first clause. (ii) second clause.",
    )
    op = LegalOperation(
        op_id="replace-clause-b-1-A-iii",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(
            path=(
                ("title", "47"),
                ("section", "227"),
                ("subsection", "b"),
                ("paragraph", "1"),
                ("subparagraph", "A"),
                ("clause", "iii"),
            )
        ),
        payload=IRNode(
            kind=IRNodeKind.SUBPARAGRAPH,
            label="iii",
            text="(iii) Replacement clause.",
        ),
    )
    outcome = _materialize_one(
        op, section.statutory_text, before_section=section
    )
    assert not isinstance(outcome, USDryRunRefusal)
    _materialized, rule_id, disposition = outcome
    assert rule_id == US_DRY_RUN_RESIDUAL_TARGET_ANCESTOR_ABSENT_IN_SOURCE_TREE_RULE_ID
    assert disposition == DISPOSITION_MISSING_SOURCE


def test_target_ancestor_absent_does_not_hide_parse_ambiguity_on_clean_tree() -> None:
    """When the ancestor exists and only the target label is missing, the section
    tree is clean and we stay with the generic node-not-located residual.
    """
    section = synthetic_usc_section(
        title=11,
        section="77",
        text="(a) First subsection. (b) Second subsection. (1) First paragraph.",
    )
    op = LegalOperation(
        op_id="replace-para-b-99",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(
            path=(
                ("title", "11"),
                ("section", "77"),
                ("subsection", "b"),
                ("paragraph", "99"),
            )
        ),
        payload=IRNode(
            kind=IRNodeKind.PARAGRAPH,
            label="99",
            text="(99) Replacement paragraph.",
        ),
    )
    outcome = _materialize_one(
        op, section.statutory_text, before_section=section
    )
    assert not isinstance(outcome, USDryRunRefusal)
    _materialized, rule_id, disposition = outcome
    assert rule_id == US_DRY_RUN_RESIDUAL_SUBSECTION_NODE_NOT_LOCATED_RULE_ID
    assert disposition == DISPOSITION_LAWVM_WRONG
