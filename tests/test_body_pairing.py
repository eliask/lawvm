"""Tests for the body-driven observed/pairing lane.

Tests cover:
1. Inventory extraction from sample amendment XML
2. Claim building from ClauseAST (primary) and legacy ops (compat)
3. Pairing: REPEAL claim blocks body use, foreign claim blocks body use,
   unmatched blocks body use
4. The should_use_body_section guard
5. Integration scenario with repeal-style amendment pattern
6. ClauseAST-from-AmendmentOps bridge
"""

from __future__ import annotations

import lxml.etree as etree

from lawvm.core.clause_ast import (
    ClauseAST,
    LabelAmend,
    RefAmend,
    ScopedBlock,
    VerbGroup,
)
from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.core.semantic_types import FacetKind, IRNodeKind, LabelAction, StructuralAction
from lawvm.finland.target_kind import TargetKind
from lawvm.finland.body_pairing import (
    ClauseClaim,
    ObservedBodyUnit,
    PayloadAssignment,
    analyze_amendment_pairing,
    assign_body_units,
    assign_body_units_subtree_aware,
    build_clause_claims,
    build_clause_claims_from_ops,
    build_observed_body_inventory,
    clause_ast_from_amendment_ops,
    enforce_pairing_invariants,
    should_use_body_section,
)
from lawvm.finland.ops import AmendmentOp
from lawvm.finland.johtolause.types import ParsedOp
from typing import Optional


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_amendment_xml(body_content: str) -> etree._Element:
    """Build a minimal amendment XML tree with the given body content."""
    xml = f"""\
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <act>
    <body>
{body_content}
    </body>
  </act>
</akomaNtoso>"""
    return etree.fromstring(xml.encode())


def _make_section(label: str, heading: str = "") -> str:
    """Build a minimal <section> XML fragment."""
    heading_el = f"<heading>{heading}</heading>" if heading else ""
    return f"""\
      <section>
        <num>{label} §</num>
        {heading_el}
        <content><p>Sisältö.</p></content>
      </section>"""


def _make_chapter_with_sections(ch_label: str, section_labels: list[str], ch_heading: str = "") -> str:
    """Build a <chapter> containing sections."""
    heading_el = f"<heading>{ch_heading}</heading>" if ch_heading else ""
    sections = "\n".join(_make_section(lbl) for lbl in section_labels)
    return f"""\
      <chapter>
        <num>{ch_label} luku</num>
        {heading_el}
{sections}
      </chapter>"""


def _make_parsed_op(
    verb: str,
    kind: str,
    number: str,
    chapter: str = "",
    momentti: int = 0,
    item: str = "",
    facet: Optional[FacetKind] = None,
) -> ParsedOp:
    """Build a ParsedOp for testing."""
    return ParsedOp(
        verb=verb,
        kind=kind,
        chapter=chapter,
        number=number,
        momentti=momentti,
        item=item,
        facet=facet,
        raw=f"{verb} {kind} {number}",
    )


# ---------------------------------------------------------------------------
# 1. Inventory extraction
# ---------------------------------------------------------------------------


class TestBuildObservedBodyInventory:
    def test_simple_sections(self) -> None:
        xml = _make_amendment_xml(_make_section("1") + "\n" + _make_section("2") + "\n" + _make_section("3"))
        inventory = build_observed_body_inventory(xml)
        assert len(inventory) == 3
        assert inventory[0].kind == IRNodeKind.SECTION.value
        assert inventory[0].label == "1"
        assert inventory[0].chapter_label == ""
        assert inventory[1].label == "2"
        assert inventory[2].label == "3"

    def test_sections_in_chapters(self) -> None:
        xml = _make_amendment_xml(
            _make_chapter_with_sections("2", ["3", "4"]) + "\n" + _make_chapter_with_sections("5", ["10", "11"])
        )
        inventory = build_observed_body_inventory(xml)

        # Should have 2 chapters + 4 sections = 6 units
        chapters = [u for u in inventory if u.kind == IRNodeKind.CHAPTER.value]
        sections = [u for u in inventory if u.kind == IRNodeKind.SECTION.value]
        assert len(chapters) == 2
        assert len(sections) == 4

        # Check chapter labels
        assert chapters[0].label == "2"
        assert chapters[1].label == "5"

        # Check section chapter context
        sec3 = next(s for s in sections if s.label == "3")
        assert sec3.chapter_label == "2"

        sec10 = next(s for s in sections if s.label == "10")
        assert sec10.chapter_label == "5"

    def test_unit_ids_are_unique(self) -> None:
        xml = _make_amendment_xml(_make_section("1") + "\n" + _make_section("1"))
        inventory = build_observed_body_inventory(xml)
        assert len(inventory) == 2
        ids = [u.unit_id for u in inventory]
        assert len(set(ids)) == 2  # unique

    def test_empty_body(self) -> None:
        xml = etree.fromstring(b"<root><body/></root>")
        inventory = build_observed_body_inventory(xml)
        assert inventory == []

    def test_no_body(self) -> None:
        xml = etree.fromstring(b"<root><preamble/></root>")
        inventory = build_observed_body_inventory(xml)
        assert inventory == []

    def test_section_without_num_skipped(self) -> None:
        xml = _make_amendment_xml("<section><content><p>No num</p></content></section>")
        inventory = build_observed_body_inventory(xml)
        assert inventory == []

    def test_letter_suffix_label(self) -> None:
        xml = _make_amendment_xml(_make_section("5 a"))
        inventory = build_observed_body_inventory(xml)
        assert len(inventory) == 1
        assert inventory[0].label == "5a"

    def test_unit_id_format_with_chapter(self) -> None:
        xml = _make_amendment_xml(_make_chapter_with_sections("3", ["7"]))
        inventory = build_observed_body_inventory(xml)
        sections = [u for u in inventory if u.kind == IRNodeKind.SECTION.value]
        assert len(sections) == 1
        assert sections[0].unit_id == "section:3/7"

    def test_malformed_section_chapter_marker_reanchors_following_sections(self) -> None:
        xml = _make_amendment_xml(
            """
      <chapter>
        <num>16 a luku</num>
        <section><num>1 §</num><content><p>chapter 16a sec1</p></content></section>
        <section><num>16 b luku</num><heading>Jakautuminen</heading></section>
        <section><num>1 §</num><content><p>chapter 16b sec1</p></content></section>
        <section><num>2 §</num><content><p>chapter 16b sec2</p></content></section>
      </chapter>
            """
        )
        inventory = build_observed_body_inventory(xml)

        chapters = [u for u in inventory if u.kind == IRNodeKind.CHAPTER.value]
        sections = [u for u in inventory if u.kind == IRNodeKind.SECTION.value]

        assert [u.label for u in chapters] == ["16a", "16b"]
        assert [(u.label, u.chapter_label) for u in sections] == [
            ("1", "16a"),
            ("1", "16b"),
            ("2", "16b"),
        ]


def test_analyze_amendment_pairing_uses_shared_sec1_acquisition_lane() -> None:
    xml = etree.fromstring(
        """
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <act>
    <formula name="enactingClause">Ympäristöministerin esittelystä säädetään:</formula>
    <body>
      <section eId="sec_1">
        <num>1 §</num>
        <content><p>muutetaan 5 § seuraavasti:</p></content>
      </section>
      <section>
        <num>5 §</num>
        <content><p>Sisältö.</p></content>
      </section>
    </body>
  </act>
</akomaNtoso>
        """.encode()
    )

    result = analyze_amendment_pairing("1994/1280", "2000/172", etree.tostring(xml, encoding="utf-8"))

    assert result is not None
    assert result.inventory_count == 2
    assert result.claimed_current == 1


# ---------------------------------------------------------------------------
# 2. Claim building
# ---------------------------------------------------------------------------


class TestBuildClauseClaimsFromOps:
    """Legacy compat path: build_clause_claims_from_ops with ParsedOp."""

    def test_replace_op(self) -> None:
        ops = [_make_parsed_op("M", "P", "5")]
        claims = build_clause_claims_from_ops(ops, "1994/1280")
        assert len(claims) == 1
        assert claims[0].target_statute == "1994/1280"
        assert claims[0].target_address == "5"
        assert claims[0].claim_kind == "REPLACE"

    def test_repeal_op(self) -> None:
        ops = [_make_parsed_op("K", "P", "3")]
        claims = build_clause_claims_from_ops(ops, "1994/1280")
        assert len(claims) == 1
        assert claims[0].claim_kind == "REPEAL"

    def test_insert_op(self) -> None:
        ops = [_make_parsed_op("L", "P", "5a")]
        claims = build_clause_claims_from_ops(ops, "1994/1280")
        assert len(claims) == 1
        assert claims[0].claim_kind == "INSERT"
        assert claims[0].target_address == "5a"

    def test_chapter_claim(self) -> None:
        ops = [_make_parsed_op("M", "L", "3 luku")]
        claims = build_clause_claims_from_ops(ops, "1994/1280")
        assert len(claims) == 1
        assert claims[0].target_address == "3"

    def test_chapter_context_preserved(self) -> None:
        ops = [_make_parsed_op("M", "P", "5", chapter="2")]
        claims = build_clause_claims_from_ops(ops, "1994/1280")
        assert len(claims) == 1
        assert claims[0].chapter == "2"

    def test_empty_number_skipped(self) -> None:
        ops = [_make_parsed_op("M", "P", "")]
        claims = build_clause_claims_from_ops(ops, "1994/1280")
        assert claims == []

    def test_part_kind_claim_produced(self) -> None:
        # Part-level ops produce claims so whole-part body payloads can be paired.
        ops = [_make_parsed_op("M", "O", "1")]
        claims = build_clause_claims_from_ops(ops, "1994/1280")
        assert len(claims) == 1
        assert claims[0].target_address == "1"
        assert claims[0].claim_kind == "REPLACE"

    def test_multiple_ops(self) -> None:
        ops = [
            _make_parsed_op("M", "P", "1"),
            _make_parsed_op("K", "P", "2"),
            _make_parsed_op("L", "P", "3"),
        ]
        claims = build_clause_claims_from_ops(ops, "1994/1280")
        assert len(claims) == 3

    def test_amendment_op_claim_uses_neutral_target_unit_kind(self) -> None:
        ops = [
            AmendmentOp(op_id="op_1", op_type="REPLACE", target_section="5", target_unit_kind="section", target_chapter="2"),
            AmendmentOp(
                op_id="op_2",
                op_type="REPLACE",
                target_section="3",
                target_kind=TargetKind.CHAPTER,
            ),
            AmendmentOp(
                op_id="op_3",
                op_type="REPLACE",
                target_section="I",
                target_kind=TargetKind.PART,
            ),
        ]

        claims = build_clause_claims_from_ops(ops, "1994/1280")

        assert [claim.target_address for claim in claims] == ["5", "3", "1"]
        assert claims[0].chapter == "2"


class TestBuildClauseClaimsFromAST:
    """Primary path: build_clause_claims with ClauseAST."""

    def _make_ast(self, verb_groups: list[VerbGroup]) -> ClauseAST:
        return ClauseAST(source_text="test", verb_groups=tuple(verb_groups))

    def _section_addr(self, label: str, chapter: str = "") -> LegalAddress:
        path: list[tuple[str, str]] = []
        if chapter:
            path.append(("chapter", chapter))
        path.append(("section", label))
        return LegalAddress(path=tuple(path))

    def _chapter_addr(self, label: str) -> LegalAddress:
        return LegalAddress(path=(("chapter", label),))

    def test_replace_ref_amend(self) -> None:
        ast = self._make_ast(
            [
                VerbGroup(
                    verb=StructuralAction.REPLACE,
                    nodes=(RefAmend(action=StructuralAction.REPLACE, target=self._section_addr("5")),),
                ),
            ]
        )
        claims = build_clause_claims(ast, "1994/1280")
        assert len(claims) == 1
        assert claims[0].target_statute == "1994/1280"
        assert claims[0].target_address == "5"
        assert claims[0].claim_kind == "REPLACE"

    def test_repeal_ref_amend(self) -> None:
        ast = self._make_ast(
            [
                VerbGroup(
                    verb=StructuralAction.REPEAL,
                    nodes=(RefAmend(action=StructuralAction.REPEAL, target=self._section_addr("3")),),
                ),
            ]
        )
        claims = build_clause_claims(ast, "1994/1280")
        assert len(claims) == 1
        assert claims[0].claim_kind == "REPEAL"
        assert claims[0].target_address == "3"

    def test_insert_ref_amend(self) -> None:
        ast = self._make_ast(
            [
                VerbGroup(
                    verb=StructuralAction.INSERT,
                    nodes=(RefAmend(action=StructuralAction.INSERT, target=self._section_addr("5a")),),
                ),
            ]
        )
        claims = build_clause_claims(ast, "1994/1280")
        assert len(claims) == 1
        assert claims[0].claim_kind == "INSERT"
        assert claims[0].target_address == "5a"

    def test_chapter_claim(self) -> None:
        ast = self._make_ast(
            [
                VerbGroup(
                    verb=StructuralAction.REPLACE,
                    nodes=(RefAmend(action=StructuralAction.REPLACE, target=self._chapter_addr("3")),),
                ),
            ]
        )
        claims = build_clause_claims(ast, "1994/1280")
        assert len(claims) == 1
        assert claims[0].target_address == "3"

    def test_chapter_context_from_path(self) -> None:
        ast = self._make_ast(
            [
                VerbGroup(
                    verb=StructuralAction.REPLACE,
                    nodes=(
                        RefAmend(
                            action=StructuralAction.REPLACE,
                            target=self._section_addr("5", chapter="2"),
                        ),
                    ),
                ),
            ]
        )
        claims = build_clause_claims(ast, "1994/1280")
        assert len(claims) == 1
        assert claims[0].chapter == "2"
        assert claims[0].target_address == "5"

    def test_scoped_block_chapter_context(self) -> None:
        """ScopedBlock propagates chapter context to children."""
        ast = self._make_ast(
            [
                VerbGroup(
                    verb=StructuralAction.REPLACE,
                    nodes=(
                        ScopedBlock(
                            scope=LegalAddress(path=(("chapter", "2"),)),
                            children=(
                                RefAmend(action=StructuralAction.REPLACE, target=self._section_addr("3")),
                                RefAmend(action=StructuralAction.REPLACE, target=self._section_addr("4")),
                            ),
                        ),
                    ),
                ),
            ]
        )
        claims = build_clause_claims(ast, "1994/1280")
        assert len(claims) == 2
        assert claims[0].chapter == "2"
        assert claims[0].target_address == "3"
        assert claims[1].chapter == "2"
        assert claims[1].target_address == "4"

    def test_empty_ast(self) -> None:
        ast = self._make_ast([])
        claims = build_clause_claims(ast, "1994/1280")
        assert claims == []

    def test_multi_verb_groups(self) -> None:
        ast = self._make_ast(
            [
                VerbGroup(
                    verb=StructuralAction.REPLACE,
                    nodes=(RefAmend(action=StructuralAction.REPLACE, target=self._section_addr("1")),),
                ),
                VerbGroup(
                    verb=StructuralAction.REPEAL,
                    nodes=(RefAmend(action=StructuralAction.REPEAL, target=self._section_addr("2")),),
                ),
                VerbGroup(
                    verb=StructuralAction.INSERT,
                    nodes=(RefAmend(action=StructuralAction.INSERT, target=self._section_addr("3")),),
                ),
            ]
        )
        claims = build_clause_claims(ast, "1994/1280")
        assert len(claims) == 3
        assert claims[0].claim_kind == "REPLACE"
        assert claims[1].claim_kind == "REPEAL"
        assert claims[2].claim_kind == "INSERT"

    def test_label_amend_renumber(self) -> None:
        ast = self._make_ast(
            [
                VerbGroup(
                    verb=StructuralAction.RENUMBER,
                    nodes=(
                        LabelAmend(
                            action=LabelAction.RENUMBER,
                            target=self._section_addr("5"),
                            new_label="6",
                        ),
                    ),
                ),
            ]
        )
        claims = build_clause_claims(ast, "1994/1280")
        assert len(claims) == 1
        assert claims[0].claim_kind == "RENUMBER"
        assert claims[0].target_address == "5"

    def test_subsection_ref_skipped(self) -> None:
        """Sub-section targets (subsection, item) are not claim-producing."""
        addr = LegalAddress(path=(("section", "5"), ("subsection", "2")))
        ast = self._make_ast(
            [
                VerbGroup(verb=StructuralAction.REPLACE, nodes=(RefAmend(action=StructuralAction.REPLACE, target=addr),)),
            ]
        )
        claims = build_clause_claims(ast, "1994/1280")
        # subsection leaf kind is not section or chapter -- no claim
        assert claims == []


# ---------------------------------------------------------------------------
# 3. Pairing: assign_body_units
# ---------------------------------------------------------------------------


class TestAssignBodyUnits:
    def test_matched_current_statute(self) -> None:
        inventory = [
            ObservedBodyUnit(unit_id="section:5", kind="section", label="5"),
        ]
        claims = [
            ClauseClaim(
                target_statute="1994/1280",
                target_address="5",
                claim_kind="REPLACE",
            ),
        ]
        assignments = assign_body_units(inventory, claims, "1994/1280")
        assert len(assignments) == 1
        assert assignments[0].status == "claimed_current"
        assert assignments[0].claim is not None

    def test_foreign_statute(self) -> None:
        inventory = [
            ObservedBodyUnit(unit_id="section:5", kind="section", label="5"),
        ]
        claims = [
            ClauseClaim(
                target_statute="2000/172",
                target_address="5",
                claim_kind="REPLACE",
            ),
        ]
        assignments = assign_body_units(inventory, claims, "1994/1280")
        assert len(assignments) == 1
        assert assignments[0].status == "claimed_foreign"

    def test_unmatched(self) -> None:
        inventory = [
            ObservedBodyUnit(unit_id="section:99", kind="section", label="99"),
        ]
        claims = [
            ClauseClaim(
                target_statute="1994/1280",
                target_address="5",
                claim_kind="REPLACE",
            ),
        ]
        assignments = assign_body_units(inventory, claims, "1994/1280")
        assert len(assignments) == 1
        assert assignments[0].status == "unmatched"

    def test_repeal_claim_still_current(self) -> None:
        """A REPEAL claim marks the section as claimed_current --
        the distinction is that should_use_body_section blocks payload use."""
        inventory = [
            ObservedBodyUnit(unit_id="section:3", kind="section", label="3"),
        ]
        claims = [
            ClauseClaim(
                target_statute="1994/1280",
                target_address="3",
                claim_kind="REPEAL",
            ),
        ]
        assignments = assign_body_units(inventory, claims, "1994/1280")
        assert len(assignments) == 1
        assert assignments[0].status == "claimed_current"
        assert assignments[0].claim is not None
        assert assignments[0].claim.claim_kind == "REPEAL"

    def test_chapter_matching(self) -> None:
        """Claims with chapter context should match only sections in that chapter.

        A claim scoped to chapter 2 must NOT match a body unit in chapter 3
        (cross-chapter misrouting prevention).
        """
        inventory = [
            ObservedBodyUnit(
                unit_id="section:2/5",
                kind="section",
                label="5",
                chapter_label="2",
            ),
            ObservedBodyUnit(
                unit_id="section:3/5",
                kind="section",
                label="5",
                chapter_label="3",
            ),
        ]
        claims = [
            ClauseClaim(
                target_statute="1994/1280",
                target_address="5",
                claim_kind="REPLACE",
                chapter="2",
            ),
        ]
        assignments = assign_body_units(inventory, claims, "1994/1280")
        assert len(assignments) == 2

        # Section in chapter 2 should be claimed
        ch2 = next(a for a in assignments if a.body_unit_id == "section:2/5")
        assert ch2.status == "claimed_current"

        # Section in chapter 3 must NOT be claimed — the claim is scoped to
        # chapter 2 only.  Cross-chapter misrouting prevention.
        ch3 = next(a for a in assignments if a.body_unit_id == "section:3/5")
        assert ch3.status == "unmatched"

    def test_no_claims_all_unmatched(self) -> None:
        inventory = [
            ObservedBodyUnit(unit_id="section:1", kind="section", label="1"),
            ObservedBodyUnit(unit_id="section:2", kind="section", label="2"),
        ]
        assignments = assign_body_units(inventory, [], "1994/1280")
        assert len(assignments) == 2
        assert all(a.status == "unmatched" for a in assignments)


# ---------------------------------------------------------------------------
# 4. Invariant enforcement
# ---------------------------------------------------------------------------


class TestEnforcePairingInvariants:
    def test_no_findings_for_current(self) -> None:
        assignments = [
            PayloadAssignment(
                body_unit_id="section:5",
                status="claimed_current",
                claim=ClauseClaim(
                    target_statute="1994/1280",
                    target_address="5",
                    claim_kind="REPLACE",
                ),
            ),
        ]
        findings = enforce_pairing_invariants(assignments, "1994/1280", "2000/172")
        assert findings == []

    def test_foreign_finding(self) -> None:
        assignments = [
            PayloadAssignment(
                body_unit_id="section:5",
                status="claimed_foreign",
                claim=ClauseClaim(
                    target_statute="OTHER/LAW",
                    target_address="5",
                    claim_kind="REPLACE",
                ),
            ),
        ]
        findings = enforce_pairing_invariants(assignments, "1994/1280", "2000/172")
        assert len(findings) == 1
        assert findings[0].kind == "foreign_body_unit"
        assert findings[0].blocking is True

    def test_unmatched_finding(self) -> None:
        assignments = [
            PayloadAssignment(
                body_unit_id="section:99",
                status="unmatched",
                claim=None,
            ),
        ]
        findings = enforce_pairing_invariants(assignments, "1994/1280", "2000/172")
        assert len(findings) == 1
        assert findings[0].kind == "unmatched_body_unit"
        assert findings[0].blocking is True

    def test_mixed_findings(self) -> None:
        assignments = [
            PayloadAssignment(
                body_unit_id="section:1",
                status="claimed_current",
                claim=ClauseClaim(
                    target_statute="1994/1280",
                    target_address="1",
                    claim_kind="REPLACE",
                ),
            ),
            PayloadAssignment(
                body_unit_id="section:5",
                status="claimed_foreign",
                claim=ClauseClaim(
                    target_statute="OTHER",
                    target_address="5",
                    claim_kind="REPLACE",
                ),
            ),
            PayloadAssignment(
                body_unit_id="section:99",
                status="unmatched",
                claim=None,
            ),
        ]
        findings = enforce_pairing_invariants(assignments, "1994/1280", "2000/172")
        assert len(findings) == 2
        kinds = {f.kind for f in findings}
        assert kinds == {"foreign_body_unit", "unmatched_body_unit"}


# ---------------------------------------------------------------------------
# 5. should_use_body_section guard
# ---------------------------------------------------------------------------


class TestShouldUseBodySection:
    def test_claimed_current_replace(self) -> None:
        assignments = [
            PayloadAssignment(
                body_unit_id="section:5",
                status="claimed_current",
                claim=ClauseClaim(
                    target_statute="1994/1280",
                    target_address="5",
                    claim_kind="REPLACE",
                ),
            ),
        ]
        assert should_use_body_section("5", "", assignments) is True

    def test_claimed_current_repeal_blocked(self) -> None:
        """REPEAL claims block payload use even though status is claimed_current."""
        assignments = [
            PayloadAssignment(
                body_unit_id="section:3",
                status="claimed_current",
                claim=ClauseClaim(
                    target_statute="1994/1280",
                    target_address="3",
                    claim_kind="REPEAL",
                ),
            ),
        ]
        assert should_use_body_section("3", "", assignments) is False

    def test_foreign_blocked(self) -> None:
        assignments = [
            PayloadAssignment(
                body_unit_id="section:5",
                status="claimed_foreign",
                claim=ClauseClaim(
                    target_statute="OTHER",
                    target_address="5",
                    claim_kind="REPLACE",
                ),
            ),
        ]
        assert should_use_body_section("5", "", assignments) is False

    def test_unmatched_allowed(self) -> None:
        """Unmatched sections are allowed — they are the body-coverage recovery case."""
        assignments = [
            PayloadAssignment(
                body_unit_id="section:99",
                status="unmatched",
                claim=None,
            ),
        ]
        assert should_use_body_section("99", "", assignments) is True

    def test_no_matching_assignment_allows(self) -> None:
        """If the section is not in the inventory at all, allow it (conservative)."""
        assignments = [
            PayloadAssignment(
                body_unit_id="section:5",
                status="claimed_current",
                claim=ClauseClaim(
                    target_statute="1994/1280",
                    target_address="5",
                    claim_kind="REPLACE",
                ),
            ),
        ]
        # Label 99 is not in assignments
        assert should_use_body_section("99", "", assignments) is True

    def test_chapter_context_matching(self) -> None:
        assignments = [
            PayloadAssignment(
                body_unit_id="section:2/5",
                status="claimed_current",
                claim=ClauseClaim(
                    target_statute="1994/1280",
                    target_address="5",
                    claim_kind="REPLACE",
                    chapter="2",
                ),
            ),
            PayloadAssignment(
                body_unit_id="section:3/5",
                status="claimed_foreign",
                claim=ClauseClaim(
                    target_statute="OTHER",
                    target_address="5",
                    claim_kind="REPLACE",
                    chapter="3",
                ),
            ),
        ]
        # Chapter 2, section 5 → allowed
        assert should_use_body_section("5", "2", assignments) is True
        # Chapter 3, section 5 → blocked (foreign)
        assert should_use_body_section("5", "3", assignments) is False


# ---------------------------------------------------------------------------
# 6. Integration: repeal amendment body content must not be used
# ---------------------------------------------------------------------------


class TestIntegrationRepealAmendment:
    """Scenario: amendment 2000/172 repeals statute 1994/1280.
    The amendment's own body has sections with the same labels as the
    repealed statute.  Those body sections must NOT be used as payload.
    """

    def test_repeal_amendment_body_blocked(self) -> None:
        # Build amendment XML with body sections
        xml = _make_amendment_xml(
            _make_section("1", "Soveltamisala")
            + "\n"
            + _make_section("2", "Määritelmät")
            + "\n"
            + _make_section("3", "Voimaantulo")
        )

        # Extract inventory
        inventory = build_observed_body_inventory(xml)
        assert len(inventory) == 3

        # The PEG parser produced REPEAL ops for the target statute
        ops = [
            _make_parsed_op("K", "P", "1"),
            _make_parsed_op("K", "P", "2"),
            _make_parsed_op("K", "P", "3"),
        ]
        claims = build_clause_claims_from_ops(ops, "1994/1280")
        assert len(claims) == 3
        assert all(c.claim_kind == "REPEAL" for c in claims)

        # Assign: all should be claimed_current (REPEAL)
        assignments = assign_body_units(inventory, claims, "1994/1280")
        assert len(assignments) == 3
        assert all(a.status == "claimed_current" for a in assignments)

        # No enforcement findings (all are claimed_current)
        findings = enforce_pairing_invariants(assignments, "1994/1280", "2000/172")
        assert findings == []

        # But should_use_body_section blocks ALL of them (REPEAL claims)
        for label in ["1", "2", "3"]:
            assert should_use_body_section(label, "", assignments) is False

    def test_mixed_replace_and_repeal(self) -> None:
        """Amendment replaces some sections and repeals others."""
        xml = _make_amendment_xml(_make_section("1") + "\n" + _make_section("2") + "\n" + _make_section("3"))
        inventory = build_observed_body_inventory(xml)

        ops = [
            _make_parsed_op("M", "P", "1"),  # REPLACE
            _make_parsed_op("K", "P", "2"),  # REPEAL
            _make_parsed_op("M", "P", "3"),  # REPLACE
        ]
        claims = build_clause_claims_from_ops(ops, "1994/1280")
        assignments = assign_body_units(inventory, claims, "1994/1280")

        # Section 1 and 3 → REPLACE → should use body
        assert should_use_body_section("1", "", assignments) is True
        assert should_use_body_section("3", "", assignments) is True

        # Section 2 → REPEAL → should NOT use body
        assert should_use_body_section("2", "", assignments) is False

    def test_omnibus_amendment_foreign_sections(self) -> None:
        """Omnibus amendment has body sections for different statutes.
        Only sections claimed for the current statute should be used.
        """
        xml = _make_amendment_xml(_make_section("1") + "\n" + _make_section("2") + "\n" + _make_section("5"))
        inventory = build_observed_body_inventory(xml)
        assert len(inventory) == 3

        # Claims: section 1 is for current statute, section 5 is for another
        claims = [
            ClauseClaim(
                target_statute="1994/1280",
                target_address="1",
                claim_kind="REPLACE",
            ),
            ClauseClaim(
                target_statute="OTHER/LAW",
                target_address="5",
                claim_kind="REPLACE",
            ),
        ]

        assignments = assign_body_units(inventory, claims, "1994/1280")

        # Section 1 → claimed_current
        sec1 = next(a for a in assignments if a.body_unit_id == "section:1")
        assert sec1.status == "claimed_current"

        # Section 5 → claimed_foreign
        sec5 = next(a for a in assignments if a.body_unit_id == "section:5")
        assert sec5.status == "claimed_foreign"

        # Section 2 → unmatched
        sec2 = next(a for a in assignments if a.body_unit_id == "section:2")
        assert sec2.status == "unmatched"

        # Invariant enforcement: both foreign and unmatched produce findings
        findings = enforce_pairing_invariants(assignments, "1994/1280", "2000/172")
        assert len(findings) == 2

        # should_use_body_section guards
        assert should_use_body_section("1", "", assignments) is True
        assert should_use_body_section("5", "", assignments) is False
        # Unmatched → allowed (body-coverage recovery case)
        assert should_use_body_section("2", "", assignments) is True


# ---------------------------------------------------------------------------
# 7. clause_ast_from_amendment_ops bridge
# ---------------------------------------------------------------------------

class TestClauseAstFromAmendmentOps:
    """Test the AmendmentOp -> ClauseAST bridge used by grafter."""

    def test_empty_ops(self) -> None:
        ast = clause_ast_from_amendment_ops([])
        assert ast.verb_groups == ()

    def test_single_replace_section(self) -> None:
        op = AmendmentOp(
            op_type="REPLACE",
            target_section="5",
            target_kind=TargetKind.SECTION,
        )
        ast = clause_ast_from_amendment_ops([op])
        assert len(ast.verb_groups) == 1
        assert ast.verb_groups[0].verb == StructuralAction.REPLACE
        assert len(ast.verb_groups[0].nodes) == 1
        node = ast.verb_groups[0].nodes[0]
        assert isinstance(node, RefAmend)
        assert node.action == StructuralAction.REPLACE
        assert node.target.leaf_kind() == "section"
        assert node.target.leaf_label() == "5"

    def test_repeal_with_chapter(self) -> None:
        op = AmendmentOp(
            op_type="REPEAL",
            target_section="3",
            target_kind=TargetKind.SECTION,
            target_chapter="2",
        )
        ast = clause_ast_from_amendment_ops([op])
        assert len(ast.verb_groups) == 1
        node = ast.verb_groups[0].nodes[0]
        assert isinstance(node, RefAmend)
        assert node.action == StructuralAction.REPEAL
        # Check chapter is in the path
        assert ("chapter", "2") in node.target.path

    def test_chapter_level_op(self) -> None:
        op = AmendmentOp(
            op_type="REPLACE",
            target_section="3",
            target_kind=TargetKind.CHAPTER,
        )
        ast = clause_ast_from_amendment_ops([op])
        node = ast.verb_groups[0].nodes[0]
        assert isinstance(node, RefAmend)
        assert node.target.leaf_kind() == "chapter"

    def test_multi_verb_groups(self) -> None:
        ops = [
            AmendmentOp(op_type="REPLACE", target_section="1", target_kind=TargetKind.SECTION),
            AmendmentOp(op_type="REPEAL", target_section="2", target_kind=TargetKind.SECTION),
            AmendmentOp(op_type="INSERT", target_section="3", target_kind=TargetKind.SECTION),
        ]
        ast = clause_ast_from_amendment_ops(ops)
        assert len(ast.verb_groups) == 3
        assert ast.verb_groups[0].verb == StructuralAction.REPLACE
        assert ast.verb_groups[1].verb == StructuralAction.REPEAL
        assert ast.verb_groups[2].verb == StructuralAction.INSERT

    def test_renumber_produces_label_amend(self) -> None:
        op = AmendmentOp(
            op_type="RENUMBER",
            target_section="5",
            target_kind=TargetKind.SECTION,
            lo=LegalOperation(
                op_id="renumber_5_to_6",
                sequence=0,
                action=StructuralAction.RENUMBER,
                target=LegalAddress(path=(("section", "5"),)),
                destination=LegalAddress(path=(("section", "6"),)),
            ),
        )
        ast = clause_ast_from_amendment_ops([op])
        node = ast.verb_groups[0].nodes[0]
        assert isinstance(node, LabelAmend)
        assert node.action == LabelAction.RENUMBER

    def test_roundtrip_claims_match(self) -> None:
        """Claims from AST bridge match claims from legacy ops path."""
        ops_parsed = [
            _make_parsed_op("M", "P", "1"),
            _make_parsed_op("K", "P", "2"),
            _make_parsed_op("M", "P", "3", chapter="5"),
        ]
        legacy_claims = build_clause_claims_from_ops(ops_parsed, "1994/1280")

        # Build equivalent AmendmentOps
        am_ops = [
            AmendmentOp(op_type="REPLACE", target_section="1", target_kind=TargetKind.SECTION),
            AmendmentOp(op_type="REPEAL", target_section="2", target_kind=TargetKind.SECTION),
            AmendmentOp(op_type="REPLACE", target_section="3", target_kind=TargetKind.SECTION, target_chapter="5"),
        ]
        ast = clause_ast_from_amendment_ops(am_ops)
        ast_claims = build_clause_claims(ast, "1994/1280")

        assert len(ast_claims) == len(legacy_claims)
        for lc, ac in zip(legacy_claims, ast_claims):
            assert lc.target_address == ac.target_address
            assert lc.claim_kind == ac.claim_kind
            assert lc.chapter == ac.chapter


# ---------------------------------------------------------------------------
# 8. analyze_amendment_pairing (standalone per-amendment analysis)
# ---------------------------------------------------------------------------

class TestAnalyzeAmendmentPairing:
    def _make_full_amendment_xml(self, body_content: str, johto_text: str) -> bytes:
        """Build a complete amendment XML with preamble and body."""
        return f"""\
<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <act>
    <preamble>
      <formula name="enactingClause">
        <p>{johto_text}</p>
      </formula>
    </preamble>
    <body>
{body_content}
    </body>
  </act>
</akomaNtoso>""".encode("utf-8")

    def test_replace_amendment_all_current(self) -> None:
        xml = self._make_full_amendment_xml(
            _make_section("5") + "\n" + _make_section("6"),
            "muutetaan 5 ja 6 §",
        )
        result = analyze_amendment_pairing("1994/1280", "2000/172", xml)
        assert result is not None
        assert result.inventory_count == 2
        assert result.claimed_current == 2
        assert result.claimed_foreign == 0
        assert result.unmatched == 0
        assert result.findings == ()
        assert not result.has_anomalies

    def test_repeal_amendment_blocked(self) -> None:
        xml = self._make_full_amendment_xml(
            _make_section("3"),
            "kumotaan 3 §",
        )
        result = analyze_amendment_pairing("1994/1280", "2000/172", xml)
        assert result is not None
        assert result.claimed_current == 1
        assert result.repeal_blocked == 1
        assert result.has_anomalies

    def test_unmatched_body_unit(self) -> None:
        xml = self._make_full_amendment_xml(
            _make_section("5") + "\n" + _make_section("99"),
            "muutetaan 5 §",
        )
        result = analyze_amendment_pairing("1994/1280", "2000/172", xml)
        assert result is not None
        assert result.unmatched == 1
        assert result.has_anomalies
        assert any(f.kind == "unmatched_body_unit" for f in result.findings)

    def test_no_body_returns_none(self) -> None:
        xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <act>
    <preamble>
      <formula name="enactingClause"><p>muutetaan 5 §</p></formula>
    </preamble>
    <body/>
  </act>
</akomaNtoso>""".encode("utf-8")
        result = analyze_amendment_pairing("1994/1280", "2000/172", xml)
        assert result is None

    def test_no_johtolause_returns_none(self) -> None:
        xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <act>
    <body>
      <section><num>5 §</num><content><p>text</p></content></section>
    </body>
  </act>
</akomaNtoso>""".encode("utf-8")
        result = analyze_amendment_pairing("1994/1280", "2000/172", xml)
        assert result is None

    def test_to_dict_roundtrip(self) -> None:
        xml = self._make_full_amendment_xml(
            _make_section("5") + "\n" + _make_section("99"),
            "muutetaan 5 §",
        )
        result = analyze_amendment_pairing("1994/1280", "2000/172", xml)
        assert result is not None
        d = result.to_dict()
        assert d["statute_id"] == "1994/1280"
        assert d["amendment_id"] == "2000/172"
        assert d["unmatched"] == 1
        assert len(d["findings"]) >= 1
        assert d["findings"][0]["kind"] == "unmatched_body_unit"


# ---------------------------------------------------------------------------
# 9. Cross-chapter INSERT misrouting prevention
# ---------------------------------------------------------------------------


class TestCrossChapterInsertMisrouting:
    """Regression test for 2008/521 (Vakuutusyhtiölaki) bug.

    Amendment 2019/518 adds INSERT chapter:5/section:18a.  The amendment body
    contains an "18a §" inside chapter 5.  The existing statute already has
    chapter:2/section:18a.  Without the fix, the body matcher routes the
    chapter 5 body unit to the chapter 2 claim (wrong chapter), corrupting
    chapter 2 and leaving chapter 5 incorrectly populated.
    """

    def test_insert_targets_correct_chapter(self) -> None:
        """INSERT chapter:5/section:18a must not claim body unit in chapter 2."""
        # Amendment body: chapter 5 has section 18a
        inventory = [
            ObservedBodyUnit(
                unit_id="section:5/18a",
                kind="section",
                label="18a",
                chapter_label="5",
            ),
        ]
        # Claims: INSERT section 18a INTO chapter 5, REPLACE section 18a IN chapter 2
        claims = [
            ClauseClaim(
                target_statute="2008/521",
                target_address="18a",
                claim_kind="INSERT",
                chapter="5",
            ),
            ClauseClaim(
                target_statute="2008/521",
                target_address="18a",
                claim_kind="REPLACE",
                chapter="2",
            ),
        ]
        assignments = assign_body_units(inventory, claims, "2008/521")
        assert len(assignments) == 1
        a = assignments[0]
        assert a.status == "claimed_current"
        assert a.claim is not None
        # The body unit in chapter 5 must match the chapter 5 INSERT claim,
        # NOT the chapter 2 REPLACE claim.
        assert a.claim.chapter == "5"
        assert a.claim.claim_kind == "INSERT"

    def test_body_units_in_different_chapters_route_correctly(self) -> None:
        """Both chapters have section 18a; each routes to its own claim."""
        inventory = [
            ObservedBodyUnit(
                unit_id="section:2/18a",
                kind="section",
                label="18a",
                chapter_label="2",
            ),
            ObservedBodyUnit(
                unit_id="section:5/18a",
                kind="section",
                label="18a",
                chapter_label="5",
            ),
        ]
        claims = [
            ClauseClaim(
                target_statute="2008/521",
                target_address="18a",
                claim_kind="REPLACE",
                chapter="2",
            ),
            ClauseClaim(
                target_statute="2008/521",
                target_address="18a",
                claim_kind="INSERT",
                chapter="5",
            ),
        ]
        assignments = assign_body_units(inventory, claims, "2008/521")
        assert len(assignments) == 2

        ch2 = next(a for a in assignments if a.body_unit_id == "section:2/18a")
        assert ch2.status == "claimed_current"
        assert ch2.claim is not None
        assert ch2.claim.chapter == "2"
        assert ch2.claim.claim_kind == "REPLACE"

        ch5 = next(a for a in assignments if a.body_unit_id == "section:5/18a")
        assert ch5.status == "claimed_current"
        assert ch5.claim is not None
        assert ch5.claim.chapter == "5"
        assert ch5.claim.claim_kind == "INSERT"

    def test_unscoped_claim_matches_any_chapter(self) -> None:
        """A claim without chapter scope (chapter='') matches any body unit."""
        inventory = [
            ObservedBodyUnit(
                unit_id="section:3/7",
                kind="section",
                label="7",
                chapter_label="3",
            ),
        ]
        claims = [
            ClauseClaim(
                target_statute="2008/521",
                target_address="7",
                claim_kind="REPLACE",
                chapter="",  # no chapter scope
            ),
        ]
        assignments = assign_body_units(inventory, claims, "2008/521")
        assert len(assignments) == 1
        assert assignments[0].status == "claimed_current"

    def test_scoped_claim_does_not_cross_chapter(self) -> None:
        """A claim scoped to chapter 5 must NOT match a body unit in chapter 2."""
        inventory = [
            ObservedBodyUnit(
                unit_id="section:2/18a",
                kind="section",
                label="18a",
                chapter_label="2",
            ),
        ]
        claims = [
            ClauseClaim(
                target_statute="2008/521",
                target_address="18a",
                claim_kind="INSERT",
                chapter="5",
            ),
        ]
        assignments = assign_body_units(inventory, claims, "2008/521")
        assert len(assignments) == 1
        # The body unit in chapter 2 must NOT be claimed by the chapter 5 INSERT
        assert assignments[0].status == "unmatched"

    def test_flat_body_unit_matches_scoped_claim(self) -> None:
        """A body unit without chapter context matches a chapter-scoped claim.

        This handles amendments where the body has flat sections (no enclosing
        chapter element) but the johtolause specifies a chapter target.
        """
        inventory = [
            ObservedBodyUnit(
                unit_id="section:18a",
                kind="section",
                label="18a",
                chapter_label="",  # no chapter context in body
            ),
        ]
        claims = [
            ClauseClaim(
                target_statute="2008/521",
                target_address="18a",
                claim_kind="INSERT",
                chapter="5",
            ),
        ]
        assignments = assign_body_units(inventory, claims, "2008/521")
        assert len(assignments) == 1
        # Flat body unit should match the chapter-scoped claim (fallback)
        assert assignments[0].status == "claimed_current"
        assert assignments[0].claim is not None
        assert assignments[0].claim.chapter == "5"

    def test_full_xml_cross_chapter_insert(self) -> None:
        """End-to-end: XML with two chapters having same section label."""
        xml = _make_amendment_xml(
            _make_chapter_with_sections("2", ["18a"])
            + "\n"
            + _make_chapter_with_sections("5", ["18a"])
        )
        inventory = build_observed_body_inventory(xml)

        # Verify inventory structure
        sections = [u for u in inventory if u.kind == IRNodeKind.SECTION.value]
        assert len(sections) == 2
        ch2_sec = next(s for s in sections if s.chapter_label == "2")
        ch5_sec = next(s for s in sections if s.chapter_label == "5")
        assert ch2_sec.label == "18a"
        assert ch5_sec.label == "18a"

        # Claims: REPLACE in chapter 2, INSERT in chapter 5
        claims = [
            ClauseClaim(
                target_statute="2008/521",
                target_address="18a",
                claim_kind="REPLACE",
                chapter="2",
            ),
            ClauseClaim(
                target_statute="2008/521",
                target_address="18a",
                claim_kind="INSERT",
                chapter="5",
            ),
        ]
        assignments = assign_body_units(inventory, claims, "2008/521")

        # Find section assignments (skip chapter units)
        sec_assignments = [
            a for a in assignments
            if a.body_unit_id.startswith("section:")
        ]
        assert len(sec_assignments) == 2

        ch2_a = next(a for a in sec_assignments if "2/" in a.body_unit_id)
        ch5_a = next(a for a in sec_assignments if "5/" in a.body_unit_id)

        assert ch2_a.status == "claimed_current"
        assert ch2_a.claim is not None
        assert ch2_a.claim.chapter == "2"

        assert ch5_a.status == "claimed_current"
        assert ch5_a.claim is not None
        assert ch5_a.claim.chapter == "5"

    def test_subtree_adoption_respects_part_scope_for_same_chapter_label(self) -> None:
        """Chapter INSERT subtree adoption must key on (part, chapter), not chapter alone."""
        xml = etree.fromstring(
            """
            <root>
              <body>
                <part>
                  <num>IV osa</num>
                  <chapter>
                    <num>2 luku</num>
                    <section><num>1 §</num></section>
                  </chapter>
                </part>
                <part>
                  <num>V osa</num>
                  <chapter>
                    <num>2 luku</num>
                    <section><num>1 §</num></section>
                  </chapter>
                </part>
              </body>
            </root>
            """
        )
        inventory = build_observed_body_inventory(xml)
        claims = [
            ClauseClaim(
                target_statute="2008/521",
                target_address="2",
                claim_kind="INSERT",
                chapter="",
                part="5",
            )
        ]

        assignments = assign_body_units_subtree_aware(inventory, claims, "2008/521")
        by_id = {a.body_unit_id: a for a in assignments}
        sec_units = [u for u in inventory if u.kind == IRNodeKind.SECTION.value]
        iv_sec = next(u for u in sec_units if u.part_label == "4")
        v_sec = next(u for u in sec_units if u.part_label == "5")

        assert by_id[iv_sec.unit_id].status == "unmatched"
        assert by_id[v_sec.unit_id].status == "claimed_current"
        assert by_id[v_sec.unit_id].claim is not None
        assert by_id[v_sec.unit_id].claim.part == "5"

    def test_part_insert_claim_adopts_part_subtree(self) -> None:
        """Part INSERT claims must adopt descendant chapters and sections."""
        xml = etree.fromstring(
            """
            <root>
              <body>
                <part>
                  <num>V osa</num>
                  <chapter>
                    <num>1 luku</num>
                    <section><num>109 §</num></section>
                  </chapter>
                  <chapter>
                    <num>2 luku</num>
                    <section><num>115 §</num></section>
                  </chapter>
                </part>
              </body>
            </root>
            """
        )
        inventory = build_observed_body_inventory(xml)
        claims = [
            ClauseClaim(
                target_statute="2001/1226",
                target_address="5",
                claim_kind="INSERT",
                chapter="",
                part="",
            )
        ]

        assignments = assign_body_units_subtree_aware(inventory, claims, "2001/1226")
        by_id = {a.body_unit_id: a for a in assignments}

        part_unit = next(u for u in inventory if u.kind == IRNodeKind.PART.value)
        chapter_units = [u for u in inventory if u.kind == IRNodeKind.CHAPTER.value]
        section_units = [u for u in inventory if u.kind == IRNodeKind.SECTION.value]

        assert by_id[part_unit.unit_id].status == "claimed_current"
        assert by_id[part_unit.unit_id].claim is not None
        assert by_id[part_unit.unit_id].claim.target_address == "5"

        assert all(by_id[u.unit_id].status == "claimed_current" for u in chapter_units)
        assert all(by_id[u.unit_id].status == "claimed_current" for u in section_units)


# ---------------------------------------------------------------------------
# 10. Tilalle-range INSERT: parent chapter adoption
# ---------------------------------------------------------------------------


class TestTilalleRangeInsertParentAdoption:
    """Tilalle-range INSERT ops insert sections into an existing chapter.

    When sections 4-8 in chapter 25 are inserted via tilalle (replacement of
    previously-repealed section slots), the body XML contains a chapter 25
    element wrapping sections 4-8.  The section-level INSERT claims correctly
    pair with body section units, but the chapter body unit has no chapter-level
    claim and would otherwise be "unmatched".

    The parent adoption rule in assign_body_units_subtree_aware promotes the
    chapter body unit to claimed_current when its child sections are claimed.
    """

    def test_chapter_adopted_via_section_insert_claims(self) -> None:
        """Chapter unit is promoted when child sections have INSERT claims."""
        xml = _make_amendment_xml(
            _make_chapter_with_sections("25", ["4", "5", "6", "7", "8"])
        )
        inventory = build_observed_body_inventory(xml)
        chapter_units = [u for u in inventory if u.kind == IRNodeKind.CHAPTER.value]
        section_units = [u for u in inventory if u.kind == IRNodeKind.SECTION.value]
        assert len(chapter_units) == 1
        assert len(section_units) == 5

        # Build INSERT claims for sections 4-8 in chapter 25 (tilalle pattern)
        ops = [
            AmendmentOp(
                op_id=f"insert_{s}",
                op_type="INSERT",
                target_section=s,
                target_kind=TargetKind.SECTION,
                target_chapter="25",
                source_statute="2015/303",
            )
            for s in ["4", "5", "6", "7", "8"]
        ]
        ast = clause_ast_from_amendment_ops(ops)
        claims = build_clause_claims(ast, "2008/521")

        # Without subtree awareness: chapter is unmatched
        basic_assignments = assign_body_units(inventory, claims, "2008/521")
        ch_assignment = next(a for a in basic_assignments if a.body_unit_id == "chapter:25")
        assert ch_assignment.status == "unmatched"

        # With subtree awareness: chapter is promoted to claimed_current
        subtree_assignments = assign_body_units_subtree_aware(inventory, claims, "2008/521")
        ch_assignment = next(a for a in subtree_assignments if a.body_unit_id == "chapter:25")
        assert ch_assignment.status == "claimed_current"

        # All section assignments remain claimed_current
        for s in ["4", "5", "6", "7", "8"]:
            sec = next(a for a in subtree_assignments if a.body_unit_id == f"section:25/{s}")
            assert sec.status == "claimed_current"
            assert sec.claim is not None
            assert sec.claim.claim_kind == "INSERT"

        # No findings (all units paired)
        findings = enforce_pairing_invariants(subtree_assignments, "2008/521", "2015/303")
        assert findings == []

    def test_chapter_adopted_via_replace_claims(self) -> None:
        """Chapter unit is promoted when child sections have REPLACE claims."""
        xml = _make_amendment_xml(
            _make_chapter_with_sections("3", ["1", "2"])
        )
        inventory = build_observed_body_inventory(xml)

        ops = [
            AmendmentOp(
                op_id=f"replace_{s}",
                op_type="REPLACE",
                target_section=s,
                target_kind=TargetKind.SECTION,
                target_chapter="3",
                source_statute="2020/100",
            )
            for s in ["1", "2"]
        ]
        ast = clause_ast_from_amendment_ops(ops)
        claims = build_clause_claims(ast, "2008/521")

        assignments = assign_body_units_subtree_aware(inventory, claims, "2008/521")
        ch_assignment = next(a for a in assignments if a.body_unit_id == "chapter:3")
        assert ch_assignment.status == "claimed_current"
        findings = enforce_pairing_invariants(assignments, "2008/521", "2020/100")
        assert findings == []

    def test_multi_chapter_tilalle_with_replace(self) -> None:
        """Multi-chapter amendment: REPLACE in ch 1, INSERT tilalle in ch 25."""
        xml = _make_amendment_xml(
            _make_chapter_with_sections("1", ["1", "2"])
            + "\n"
            + _make_chapter_with_sections("25", ["4", "5", "6", "7", "8"])
            + "\n"
            + _make_chapter_with_sections("30", ["10"])
        )
        inventory = build_observed_body_inventory(xml)

        ops = [
            AmendmentOp(op_id="r1", op_type="REPLACE", target_section="1", target_kind=TargetKind.SECTION, target_chapter="1", source_statute="2015/303"),
            AmendmentOp(op_id="r2", op_type="REPLACE", target_section="2", target_kind=TargetKind.SECTION, target_chapter="1", source_statute="2015/303"),
        ]
        for sec in ["4", "5", "6", "7", "8"]:
            ops.append(AmendmentOp(op_id=f"i{sec}", op_type="INSERT", target_section=sec, target_kind=TargetKind.SECTION, target_chapter="25", source_statute="2015/303"))
        ops.append(AmendmentOp(op_id="r10", op_type="REPLACE", target_section="10", target_kind=TargetKind.SECTION, target_chapter="30", source_statute="2015/303"))

        ast = clause_ast_from_amendment_ops(ops)
        claims = build_clause_claims(ast, "2008/521")

        assignments = assign_body_units_subtree_aware(inventory, claims, "2008/521")

        # All 3 chapters should be claimed_current (via parent adoption)
        for ch in ["1", "25", "30"]:
            ch_a = next(a for a in assignments if a.body_unit_id == f"chapter:{ch}")
            assert ch_a.status == "claimed_current", f"chapter:{ch} should be claimed_current"

        # All sections should be claimed_current
        for a in assignments:
            assert a.status == "claimed_current", f"{a.body_unit_id} should be claimed_current"

        # No findings
        findings = enforce_pairing_invariants(assignments, "2008/521", "2015/303")
        assert findings == []

    def test_chapter_not_adopted_when_no_section_claims(self) -> None:
        """Chapter with no claimed sections stays unmatched."""
        xml = _make_amendment_xml(
            _make_chapter_with_sections("25", ["4", "5"])
        )
        inventory = build_observed_body_inventory(xml)

        # No claims at all
        claims: list[ClauseClaim] = []
        assignments = assign_body_units_subtree_aware(inventory, claims, "2008/521")

        ch_assignment = next(a for a in assignments if a.body_unit_id == "chapter:25")
        assert ch_assignment.status == "unmatched"

    def test_chapter_not_adopted_when_sections_are_foreign(self) -> None:
        """Chapter stays unmatched when its sections are claimed by a foreign statute."""
        xml = _make_amendment_xml(
            _make_chapter_with_sections("25", ["4"])
        )
        inventory = build_observed_body_inventory(xml)

        # Claim for a different statute
        claims = [
            ClauseClaim(
                target_statute="OTHER/LAW",
                target_address="4",
                claim_kind="REPLACE",
                chapter="25",
            )
        ]
        assignments = assign_body_units_subtree_aware(inventory, claims, "2008/521")

        ch_assignment = next(a for a in assignments if a.body_unit_id == "chapter:25")
        assert ch_assignment.status == "unmatched"

        sec_assignment = next(a for a in assignments if a.body_unit_id == "section:25/4")
        assert sec_assignment.status == "claimed_foreign"

    def test_chapter_not_adopted_when_some_child_sections_are_unmatched(self) -> None:
        """Parent adoption requires full current-statute section coverage."""
        xml = _make_amendment_xml(
            _make_chapter_with_sections("25", ["4", "5"])
        )
        inventory = build_observed_body_inventory(xml)

        claims = [
            ClauseClaim(
                target_statute="2008/521",
                target_address="4",
                claim_kind="INSERT",
                chapter="25",
            )
        ]
        assignments = assign_body_units_subtree_aware(inventory, claims, "2008/521")

        ch_assignment = next(a for a in assignments if a.body_unit_id == "chapter:25")
        assert ch_assignment.status == "unmatched"

        sec4_assignment = next(a for a in assignments if a.body_unit_id == "section:25/4")
        assert sec4_assignment.status == "claimed_current"

        sec5_assignment = next(a for a in assignments if a.body_unit_id == "section:25/5")
        assert sec5_assignment.status == "unmatched"

        findings = enforce_pairing_invariants(assignments, "2008/521", "2015/303")
        assert any(f.kind == "chapter_parent_adoption_mixed_children" for f in findings)
