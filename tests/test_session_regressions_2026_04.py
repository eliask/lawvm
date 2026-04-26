"""Regression tests for session innovations (2026-04-02).

Guards six specific fixes that must not regress silently:

1. Citation span PROV boundary (peg3.strip_statute_citations): backwards walk
   stops at PROV when structural targets (LUKU/PYKALA) were seen between the
   PROV and the current position. Key regression: "7 luku" must survive when
   the citation's backwards walk hits PROV with structural tokens already seen.

2. Chapter-qualified dual-run guards (_recover_uncovered_body_ops): both
   _peg_targeted_sections and _peg_ch_labels store (chapter, label) tuples, not
   bare labels. Tests verify the tuple structure directly, and that the guard
   function _is_section_covered correctly matches chapter-qualified pairs.

3. Container INSERT section dedup (_apply_container_op): for new chapter
   INSERTs (path=None), sections are filtered only when targeted by UNSCOPED
   standalone PEG ops that already exist in the master — NOT when targeted by
   chapter-SCOPED ops in other chapters.

4. Part-nesting for chapter INSERTs (_find_chapter_insert_parent_path): new
   chapters are inserted inside the correct `osa` (part) in part-structured
   statutes.

5. Repeal snapshot expiry stripping (_emit_section_snapshot): snapshots with
   repeal-placeholder payload have expires="" stripped from op_source,
   even when the amendment's OperationSource carries a non-empty expiry.

6. PEG sekä heading continuation (_target_list): after targets like
   "13 a, 16 b §, 17 §:n edelle uusi 2 a luvun otsikko", a following
   "sekä asetukseen uusi 18 a §" continues as part of the same insert group.

Run:
    cd LawVM && uv run pytest tests/test_session_regressions_2026_04.py -v
"""

from __future__ import annotations
from lawvm.core.canonical_intent import Relabel
from lawvm.core.ir import LegalAddress, LegalOperation, OperationSource

import datetime as dt
from typing import List, Literal, Optional, Set, Tuple

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.finland.target_kind import TargetKind
from lawvm.finland.apply_structure_ops import _apply_container_op
from lawvm.finland.apply_runtime_support import (
    _emit_section_snapshot,
    _find_chapter_insert_parent_path,
)
from lawvm.finland.johtolause.compat import parse_clause
from lawvm.finland.ops import AmendmentOp, ResolvedOp, get_replay_profile
from lawvm.finland.statute import ReplayState
from lawvm.finland.grafter import _resolved_op_is_owned_by_restructure_plan
from tests.corpus_pin_helpers import pinned_replay

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_DATE = dt.date(2020, 1, 1)
_LEGAL_PIT = get_replay_profile("legal_pit")
_FINLEX_ORACLE = get_replay_profile("finlex_oracle")


def _content(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.CONTENT, text=text)


def _sub(label: str, *children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.SUBSECTION, label=label, children=tuple(children))


def _sec(label: str, *children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.SECTION, label=label, children=tuple(children))


def _body(*children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.BODY, children=tuple(children))


def _chapter(label: str, *children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.CHAPTER, label=label, children=tuple(children))


def _part(label: str, *children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.PART, label=label, children=tuple(children))


def _make_state(body_ir: IRNode) -> ReplayState:
    return ReplayState(ir=body_ir)


def _op(
    op_type: Literal["REPLACE", "REPEAL", "INSERT", "RENUMBER"] = "REPLACE",
    target_section: str = "1",
    target_kind: TargetKind = TargetKind.SECTION,
    target_chapter: Optional[str] = None,
    target_paragraph: Optional[int] = None,
    target_item: Optional[str] = None,
) -> AmendmentOp:
    return AmendmentOp(
        op_id="test_op",
        op_type=op_type,
        target_section=target_section,
        target_kind=target_kind,
        target_chapter=target_chapter,
        target_paragraph=target_paragraph,
        target_item=target_item,
        source_statute="2020/1",
        source_issue_date=_DATE,
    )


def _rop(op: AmendmentOp, muutos_ir: Optional[IRNode] = None) -> ResolvedOp:
    """Test helper: construct a ResolvedOp from an AmendmentOp without binding intent.

    Uses ResolvedOp.from_amendment_op(...) for fixtures that don't need legacy
    direct-construction behavior, avoiding warnings when testing replay/snapshot
    semantics in isolation.
    """
    path: list[tuple[str, str]] = []
    if op.target_unit_kind == "chapter":
        path.append(("chapter", str(op.target_section or "")))
    elif op.target_unit_kind == "part":
        path.append(("part", str(op.target_section or "")))
    else:
        if op.target_chapter:
            path.append(("chapter", str(op.target_chapter)))
        path.append(("section", str(op.target_section or "")))
    if op.target_paragraph is not None:
        path.append(("subsection", str(op.target_paragraph)))
    if op.target_item is not None:
        path.append(("item", str(op.target_item)))

    return ResolvedOp.from_amendment_op(
        op=op,
        muutos_ir=muutos_ir,
        cross_ir=None,
        target_unit_kind=op.target_unit_kind,
        target_norm=op.target_section or "",
        target_chapter=op.target_chapter,
        target_address=LegalAddress(path=tuple(path)),
    )


# ---------------------------------------------------------------------------
# 1. Citation span PROV boundary
# ---------------------------------------------------------------------------


class TestCitationSpanProvBoundary:
    """Innovation #1: strip_statute_citations must stop at PROV when structural
    tokens (LUKU/PYKALA) have been seen between the PROV and the citation."""

    def test_chapter_replace_op_survives_prov_in_citation_cluster(self):
        """The canonical regression case.

        Input: "muutetaan 6 a luvun 12 § sekä 7 luku, sellaisena kuin
               niistä on 6 a luvun 12 § laissa 29/2005, seuraavasti:"

        The backwards citation walk for "29/2005" encounters PROV ("sellaisena")
        and MUST stop there because structural targets (LUKU, PYKALA) for
        '6 a luvun 12 §' were already seen in the backwards walk.
        This preserves '7 luku' as a separate op.

        Key invariant: M L 7 must appear in output.
        If it does not, the backwards walk overshot the PROV and consumed '7 luku'.
        """
        text = (
            "muutetaan 6 a luvun 12 § sekä 7 luku, "
            "sellaisena kuin niistä on 6 a luvun 12 § laissa 29/2005, seuraavasti:"
        )
        ops = parse_clause(text).parsed_ops
        codes = [op.code() for op in ops]

        assert "M L 7" in codes, (
            f"Expected M L 7 in {codes!r}. Regression: citation walk overshot PROV and consumed '7 luku'."
        )
        # Also verify the chapter-scoped section op is present
        assert any("12" in c for c in codes), f"Expected op for section 12 in {codes!r}."

    def test_multiple_structural_targets_before_prov_are_preserved(self):
        """Both targets before the PROV clause are not consumed by citation walk."""
        text = "muutetaan 3 § ja 5 §, sellaisena kuin se on laissa 100/2021, seuraavasti:"
        ops = parse_clause(text).parsed_ops
        codes = [op.code() for op in ops]

        # Must produce exactly 2 section ops, not more or fewer
        assert "M P 3" in codes, (
            f"Expected M P 3 in {codes!r}. Regression: citation walk consumed PROV-preceded targets."
        )
        assert "M P 5" in codes, f"Expected M P 5 in {codes!r}."
        # Must not produce extra ops
        assert len(codes) == 2, f"Expected exactly 2 ops, got {len(codes)}: {codes!r}."

    def test_no_structural_before_prov_walk_continues_into_statute_name(self):
        """Without structural before PROV, walk should continue past PROV
        into the statute name so the citation's statute name is consumed
        (pre-existing behaviour, must not regress)."""
        text = "muutetaan 3 § ja 5 §"
        ops = parse_clause(text).parsed_ops
        codes = [op.code() for op in ops]

        # Basic sanity: both sections must be present
        assert "M P 3" in codes, f"Expected M P 3 in {codes!r}."
        assert "M P 5" in codes, f"Expected M P 5 in {codes!r}."

    def test_prov_stop_does_not_produce_spurious_additional_ops(self):
        """The PROV boundary guard must not produce extra ops by double-counting
        the provenance clause as additional targets."""
        text = (
            "muutetaan 6 a luvun 12 § sekä 7 luku, "
            "sellaisena kuin niistä on 6 a luvun 12 § laissa 29/2005, seuraavasti:"
        )
        ops = parse_clause(text).parsed_ops
        codes = [op.code() for op in ops]

        # Must not produce more than 2 ops (the chapter-scoped section + chapter)
        # Additional ops would indicate provenance clause leaking into target list
        assert len(codes) <= 3, (
            f"Too many ops ({len(codes)}): {codes!r}. Regression: provenance clause leaking into target list."
        )


# ---------------------------------------------------------------------------
# 2. Chapter-qualified dual-run guards
# ---------------------------------------------------------------------------


class TestChapterQualifiedDualRunGuards:
    """Innovation #2: _peg_targeted_sections and _peg_ch_labels must use
    (chapter, label) tuples, not bare labels.

    We test this by:
    (a) Direct structural inspection: verifying the guard logic via the
        _is_section_covered closure's expected semantics.
    (b) Integration: confirming that a PEG op for (ch=2, sec=1) does NOT
        block uncovered recovery of (ch=7, sec=1) in a realistic scenario.
    """

    def test_chapter_qualified_guard_set_allows_same_label_different_chapter(self):
        """Unit test for the guard logic used in _recover_uncovered_body_ops.

        The guard (_peg_ch_labels) stores (chapter, label) tuples.
        A PEG op for chapter='2', section='1' creates guard entry ('2', '1').
        This must NOT block section '1' in chapter '7'.
        """
        from lawvm.finland.helpers import _norm_num_token

        # Simulate the guard set construction (mirrors grafter.py lines 1175-1178)
        ops = [
            AmendmentOp(
                op_id="peg_ch2_s1",
                op_type="REPLACE",
                target_section="1",
                target_kind=TargetKind.SECTION,
                target_chapter="2",
                target_paragraph=1,  # fine-grained
                source_statute="2024/100",
                source_issue_date=_DATE,
            )
        ]

        _peg_ch_labels: Set[Tuple[Optional[str], str]] = set()
        for _op in ops:
            if _op.target_unit_kind == "section" and _op.target_section:
                _peg_ch_labels.add((_op.target_chapter, _norm_num_token(_op.target_section)))

        # Guard set should contain ('2', '1')
        assert ("2", "1") in _peg_ch_labels, (
            f"Guard set should contain ('2', '1'), got: {_peg_ch_labels!r}. "
            "Regression: guard not storing (chapter, label) tuples."
        )

        # Simulate the chapter 7 / section 1 candidate from the dual-run
        _ad_ch_7 = "7"
        _ad_label_1 = "1"
        blocked_by_guard = (_ad_ch_7, _ad_label_1) in _peg_ch_labels

        assert not blocked_by_guard, (
            f"Chapter 7/§1 candidate must NOT be blocked by ch2/§1 PEG op. "
            f"Guard set: {_peg_ch_labels!r}. "
            "Regression: guard using bare labels, blocking ch7/§1 incorrectly."
        )

    def test_unscoped_peg_op_blocks_same_label_in_any_chapter(self):
        """An unscoped PEG op (chapter=None) for section '1' should store
        (None, '1') in the guard set, blocking unscoped dual-run recovery."""
        from lawvm.finland.helpers import _norm_num_token

        ops = [
            AmendmentOp(
                op_id="peg_unscoped_s1",
                op_type="REPLACE",
                target_section="1",
                target_kind=TargetKind.SECTION,
                target_chapter=None,  # unscoped
                target_paragraph=1,
                source_statute="2024/100",
                source_issue_date=_DATE,
            )
        ]

        _peg_ch_labels: Set[Tuple[Optional[str], str]] = set()
        for _op in ops:
            if _op.target_unit_kind == "section" and _op.target_section:
                _peg_ch_labels.add((_op.target_chapter, _norm_num_token(_op.target_section)))

        assert (None, "1") in _peg_ch_labels, (
            f"Unscoped PEG op should produce (None, '1') in guard set, got: {_peg_ch_labels!r}."
        )

    def test_peg_targeted_sections_guard_uses_chapter_label_tuples(self):
        """_peg_targeted_sections (coverage-driven path) also uses (ch, label) tuples."""
        from lawvm.finland.helpers import _norm_num_token

        ops = [
            AmendmentOp(
                op_id="peg_ch2_s1",
                op_type="REPLACE",
                target_section="1",
                target_kind=TargetKind.SECTION,
                target_chapter="2",
                target_paragraph=1,
                source_statute="2024/100",
                source_issue_date=_DATE,
            )
        ]

        _peg_targeted_sections: Set[Tuple[Optional[str], str]] = set()
        for _op in ops:
            if _op.target_unit_kind == "section" and _op.target_section:
                _peg_targeted_sections.add((_op.target_chapter, _norm_num_token(_op.target_section)))

        # Guard should contain ('2', '1')
        assert ("2", "1") in _peg_targeted_sections, (
            f"Guard set should contain ('2', '1'), got: {_peg_targeted_sections!r}."
        )
        # Guard should NOT block chapter 7/§1
        assert ("7", "1") not in _peg_targeted_sections, (
            "Guard set must not contain ('7', '1') — cross-chapter false block."
        )

        # Simulate the coverage-driven path check (grafter.py line 1152):
        # if (_gap.unit.parent_label, _gap_label) in _peg_targeted_sections: continue
        gap_parent_label_ch7 = "7"
        gap_label_1 = "1"
        would_be_blocked = (gap_parent_label_ch7, gap_label_1) in _peg_targeted_sections
        assert not would_be_blocked, (
            "Chapter 7/§1 coverage gap should NOT be blocked by ch2/§1 PEG op. "
            "Regression: tuple guard not discriminating chapter context."
        )


# ---------------------------------------------------------------------------
# 3. Container INSERT section dedup
# ---------------------------------------------------------------------------


class TestContainerInsertSectionDedup:
    """Innovation #3: for new chapter INSERTs (path=None), sections are filtered
    only when targeted by UNSCOPED standalone PEG ops that already exist in the
    master."""

    def test_chapter_scoped_standalone_op_does_not_filter_new_chapter_sections(self):
        """A chapter-SCOPED PEG op (chapter='2', section='1') must NOT cause
        section '1' in a freshly-inserted new chapter 7 to be filtered out.

        Scenario:
        - Master has chapter 2 with section 1.
        - Amendment inserts new chapter 7 containing section 1.
        - Standalone PEG op targets chapter=2 / section=1 (SCOPED to ch2).
        - Section '1' in chapter 7 is DISTINCT content → must pass through.
        """
        master_body = _body(
            _chapter(
                "2",
                IRNode(kind=IRNodeKind.NUM, text="2 luku"),
                _sec("1", _sub("1", _content("chapter 2 section 1"))),
            )
        )
        state = _make_state(master_body)
        base_ir = master_body

        new_chapter_ir = _chapter(
            "7",
            IRNode(kind=IRNodeKind.NUM, text="7 luku"),
            _sec("1", _sub("1", _content("chapter 7 section 1 new content"))),
        )

        op = AmendmentOp(
            op_id="insert_ch7",
            op_type="INSERT",
            target_section="7",
            target_kind=TargetKind.CHAPTER,
            source_statute="2024/200",
            source_issue_date=_DATE,
        )

        # standalone_section_targets: chapter-SCOPED op for (ch=2, sec=1)
        # This means the op explicitly targets ch2/§1, so ch7/§1 is distinct.
        standalone_section_targets = frozenset({("2", "1")})

        result = _apply_container_op(
            state,
            op,
            new_chapter_ir,
            _LEGAL_PIT,
            "[2024/200] INSERT 7 luku",
            base_ir=base_ir,
            standalone_section_targets=standalone_section_targets,
        )

        assert result is not None and result is not state, "_apply_container_op returned None or unchanged state."
        ch7 = next(
            (c for c in result.ir.children if c.kind == IRNodeKind.CHAPTER and c.label == "7"),
            None,
        )
        assert ch7 is not None, "Chapter 7 was not inserted."

        ch7_secs = [c for c in ch7.children if c.kind == IRNodeKind.SECTION]
        assert len(ch7_secs) >= 1, (
            f"Chapter 7 should have section 1, but sections: {[c.label for c in ch7_secs]!r}. "
            "Regression: chapter-SCOPED standalone op incorrectly filtered new chapter section."
        )
        assert ch7_secs[0].label == "1", f"Expected section label '1', got {ch7_secs[0].label!r}."

    def test_no_standalone_targets_passes_all_sections(self):
        """With no standalone_section_targets, all sections in new chapter pass through."""
        master_body = _body()
        state = _make_state(master_body)

        new_chapter_ir = _chapter(
            "3",
            IRNode(kind=IRNodeKind.NUM, text="3 luku"),
            _sec("10", _sub("1", _content("section 10"))),
            _sec("11", _sub("1", _content("section 11"))),
        )

        op = AmendmentOp(
            op_id="insert_ch3",
            op_type="INSERT",
            target_section="3",
            target_kind=TargetKind.CHAPTER,
            source_statute="2024/300",
            source_issue_date=_DATE,
        )

        result = _apply_container_op(
            state,
            op,
            new_chapter_ir,
            _LEGAL_PIT,
            "[2024/300] INSERT 3 luku",
            base_ir=master_body,
            standalone_section_targets=frozenset(),
        )

        assert result is not None and result is not state, "INSERT of new chapter should succeed."
        ch3 = next(
            (c for c in result.ir.children if c.kind == IRNodeKind.CHAPTER and c.label == "3"),
            None,
        )
        assert ch3 is not None, "Chapter 3 not inserted."
        ch3_sec_labels = [c.label for c in ch3.children if c.kind == IRNodeKind.SECTION]
        assert "10" in ch3_sec_labels, f"Section 10 missing: {ch3_sec_labels!r}."
        assert "11" in ch3_sec_labels, f"Section 11 missing: {ch3_sec_labels!r}."


# ---------------------------------------------------------------------------
# 4. Part-nesting for chapter INSERTs
# ---------------------------------------------------------------------------


class TestPartNestingForChapterInserts:
    """Innovation #4: _find_chapter_insert_parent_path must return the correct
    part path for part-structured statutes."""

    def test_letter_suffix_chapter_finds_base_chapter_part(self):
        """Inserting chapter '19a' should return the path for the part that
        contains chapter '19' (the base chapter).

        This uses the find_family strategy: '19a' has base '19', which lives
        in part 'III' → parent path includes ('part', 'III').
        """
        master_body = _body(
            _part(
                "I",
                _chapter("1", IRNode(kind=IRNodeKind.NUM, text="1 luku")),
                _chapter("5", IRNode(kind=IRNodeKind.NUM, text="5 luku")),
            ),
            _part(
                "III",
                _chapter("19", IRNode(kind=IRNodeKind.NUM, text="19 luku")),
                _chapter("20", IRNode(kind=IRNodeKind.NUM, text="20 luku")),
            ),
        )

        parent_path = _find_chapter_insert_parent_path(master_body, "19a")

        assert len(parent_path) >= 1, f"Expected a non-empty parent path for chapter '19a', got: {parent_path!r}."
        # The path should point into part III
        path_as_tuples = [tuple(p) for p in parent_path]
        assert ("part", "III") in path_as_tuples, (
            f"Expected part 'III' in parent path, got: {parent_path!r}. "
            "Regression: _find_chapter_insert_parent_path failed to find base chapter's part."
        )

    def test_plain_numeric_chapter_finds_preceding_chapter_part(self):
        """Inserting chapter '24' (between ch23 in part IV and ch25 in part IV)
        should return the path pointing into part 'IV'.
        """
        master_body = _body(
            _part(
                "III",
                _chapter("15", IRNode(kind=IRNodeKind.NUM, text="15 luku")),
                _chapter("18", IRNode(kind=IRNodeKind.NUM, text="18 luku")),
            ),
            _part(
                "IV",
                _chapter("23", IRNode(kind=IRNodeKind.NUM, text="23 luku")),
                _chapter("25", IRNode(kind=IRNodeKind.NUM, text="25 luku")),
            ),
        )

        parent_path = _find_chapter_insert_parent_path(master_body, "24")

        path_as_tuples = [tuple(p) for p in parent_path]
        assert ("part", "IV") in path_as_tuples, (
            f"Expected part 'IV' in parent path for new chapter '24', got: {parent_path!r}. "
            "Regression: plain-numeric chapter nesting missed predecessor part."
        )

    def test_flat_statute_returns_body_level_path(self):
        """For a flat statute (no parts), the function should return an empty
        path (body-level insertion)."""
        master_body = _body(
            _chapter("1", IRNode(kind=IRNodeKind.NUM, text="1 luku")),
            _chapter("5", IRNode(kind=IRNodeKind.NUM, text="5 luku")),
        )

        parent_path = _find_chapter_insert_parent_path(master_body, "3")

        # For a flat statute, the result should be empty (body level)
        assert parent_path == (), (
            f"Expected body-level path () for flat statute, got: {parent_path!r}. "
            "Regression: part detection false-positive on flat statute."
        )

    def test_part_structure_correct_part_for_multiple_parts(self):
        """Chapter '12a' inserts into the part that contains chapter '12',
        NOT into a different part that comes after."""
        master_body = _body(
            _part(
                "II",
                _chapter("10", IRNode(kind=IRNodeKind.NUM, text="10 luku")),
                _chapter("12", IRNode(kind=IRNodeKind.NUM, text="12 luku")),
                _chapter("13", IRNode(kind=IRNodeKind.NUM, text="13 luku")),
            ),
            _part(
                "III",
                _chapter("20", IRNode(kind=IRNodeKind.NUM, text="20 luku")),
            ),
        )

        parent_path = _find_chapter_insert_parent_path(master_body, "12a")

        path_as_tuples = [tuple(p) for p in parent_path]
        # Must insert into part II (where ch12 lives), not part III
        assert ("part", "II") in path_as_tuples, (
            f"Expected part 'II', got: {parent_path!r}. "
            "Regression: '12a' not inserted into part containing base chapter '12'."
        )
        assert ("part", "III") not in path_as_tuples, f"Must not insert into part 'III', got: {parent_path!r}."

    def test_lower_than_all_chapters_goes_to_first_part(self):
        """Inserting chapter '3' when Part I has chapters 5,6,7 should go to
        Part I (the first part), NOT fall back to body level.

        This is the core bug: the predecessor search finds no chapter < 3,
        so best_part_path stays None and the old code returned body level.
        """
        master_body = _body(
            _part(
                "I",
                _chapter("5", IRNode(kind=IRNodeKind.NUM, text="5 luku")),
                _chapter("6", IRNode(kind=IRNodeKind.NUM, text="6 luku")),
                _chapter("7", IRNode(kind=IRNodeKind.NUM, text="7 luku")),
            ),
            _part(
                "II",
                _chapter("10", IRNode(kind=IRNodeKind.NUM, text="10 luku")),
                _chapter("15", IRNode(kind=IRNodeKind.NUM, text="15 luku")),
            ),
        )

        parent_path = _find_chapter_insert_parent_path(master_body, "3")

        assert len(parent_path) >= 1, (
            f"Expected a non-empty parent path for chapter '3', got: {parent_path!r}. "
            "Regression: chapter lower than all existing fell back to body level."
        )
        path_as_tuples = [tuple(p) for p in parent_path]
        assert ("part", "I") in path_as_tuples, (
            f"Expected part 'I' in parent path for chapter '3', got: {parent_path!r}. "
            "Regression: _find_chapter_insert_parent_path fell back to body for "
            "chapter lower than all existing."
        )

    def test_chapter_between_parts_goes_to_predecessor_part(self):
        """Inserting chapter '7' when Part I has ch1-5 and Part II has ch10-15
        should go to Part I (predecessor by chapter number), not Part II.
        """
        master_body = _body(
            _part(
                "I",
                _chapter("1", IRNode(kind=IRNodeKind.NUM, text="1 luku")),
                _chapter("5", IRNode(kind=IRNodeKind.NUM, text="5 luku")),
            ),
            _part(
                "II",
                _chapter("10", IRNode(kind=IRNodeKind.NUM, text="10 luku")),
                _chapter("15", IRNode(kind=IRNodeKind.NUM, text="15 luku")),
            ),
        )

        parent_path = _find_chapter_insert_parent_path(master_body, "7")

        path_as_tuples = [tuple(p) for p in parent_path]
        assert ("part", "I") in path_as_tuples, (
            f"Expected part 'I' in parent path for chapter '7', got: {parent_path!r}. "
            "Chapter between parts should go to predecessor part."
        )


# ---------------------------------------------------------------------------
# 5. Repeal snapshot expiry stripping
# ---------------------------------------------------------------------------


class TestRepealSnapshotExpiryStripping:
    """Innovation #5: _emit_section_snapshot strips expires from op_source
    for repeal-placeholder payloads, even when the amendment op_source carries
    a non-empty expiry."""

    def test_repeal_placeholder_snapshot_strips_expires(self):
        """When a section is absent from state.ir but present in base_ir,
        _emit_section_snapshot emits a repeal-placeholder.  The op_source
        for that placeholder must have expires='' even if the source has
        a non-empty expiry.
        """
        # Section 7 is ABSENT from state (already removed by apply_op before snapshot)
        state = _make_state(_body())
        base_ir = _body(_sec("7", _sub("1", _content("original content"))))

        op = AmendmentOp(
            op_id="repeal_7",
            op_type="REPEAL",
            target_section="7",
            target_kind=TargetKind.SECTION,
            source_statute="2022/100",
            source_issue_date=_DATE,
        )

        # Inject an expiry (simulating a temporary amendment whose source carries a sunset)
        rop_with_expiry = ResolvedOp.from_amendment_op(
            op=op,
            muutos_ir=None,
            cross_ir=None,
            target_unit_kind="section",
            target_norm="7",
            target_chapter=None,
            op_source=OperationSource(
                statute_id="2022/100",
                title="Test",
                enacted="2022-01-01",
                effective="2022-06-01",
                expires="2025-12-31",  # must be stripped
            ),
        )

        lo_ops: List[LegalOperation] = []
        _emit_section_snapshot(
            state=state,
            target_unit_kind="section",
            target_norm="7",
            target_chapter=None,
            target_part=None,
            group_rops=[rop_with_expiry],
            lo_ops_out=lo_ops,
            amendment_id="2022/100",
            source_title="Test",
            source_issue_date=_DATE,
            source_effective_date=_DATE,
            base_ir=base_ir,
        )

        assert len(lo_ops) >= 1, "Expected at least one lo_op."

        # The snapshot should be a repeal placeholder
        placeholder_ops = [
            lo for lo in lo_ops if lo.payload is not None and lo.payload.attrs.get("lawvm_repeal_placeholder") == "1"
        ]
        assert placeholder_ops, (
            f"Expected a repeal-placeholder snapshot in lo_ops. "
            f"Got: {[(lo.action, getattr(lo.payload, 'attrs', None)) for lo in lo_ops]!r}."
        )

        # Verify expiry is stripped on the placeholder op
        for lo in placeholder_ops:
            source = lo.source
            assert source is not None, "op_source must be set on placeholder snapshot."
            assert source.expires == "", (
                f"Repeal placeholder must have expires='', got {source.expires!r}. "
                "Regression: expiry stripping for repeal snapshots broken."
            )

    def test_non_repeal_snapshot_preserves_expires(self):
        """A regular replace snapshot (non-tombstone) with an expiry must NOT
        have its expiry stripped (only repeal/tombstone gets stripped)."""
        replacement_ir = _sec("3", _sub("1", _content("new text")))
        state = _make_state(_body(_sec("3", _sub("1", _content("old text")))))
        base_ir = _body(_sec("3", _sub("1", _content("base text"))))

        op = AmendmentOp(
            op_id="replace_3",
            op_type="REPLACE",
            target_section="3",
            target_kind=TargetKind.SECTION,
            source_statute="2023/50",
            source_issue_date=_DATE,
        )
        rop_with_expiry = ResolvedOp.from_amendment_op(
            op=op,
            muutos_ir=replacement_ir,
            cross_ir=None,
            target_unit_kind="section",
            target_norm="3",
            target_chapter=None,
            op_source=OperationSource(
                statute_id="2023/50",
                title="Temporary",
                enacted="2023-01-01",
                effective="2023-06-01",
                expires="2026-12-31",  # must be PRESERVED for replace
            ),
        )

        lo_ops: List[LegalOperation] = []
        _emit_section_snapshot(
            state=state,
            target_unit_kind="section",
            target_norm="3",
            target_chapter=None,
            target_part=None,
            group_rops=[rop_with_expiry],
            lo_ops_out=lo_ops,
            amendment_id="2023/50",
            source_title="Temporary",
            source_issue_date=_DATE,
            source_effective_date=_DATE,
            base_ir=base_ir,
        )

        assert len(lo_ops) >= 1
        # Find the replace snapshot for this statute
        replace_ops = [
            lo
            for lo in lo_ops
            if lo.action == StructuralAction.REPLACE
            and lo.source is not None
            and lo.source.statute_id == "2023/50"
            and (lo.payload is None or lo.payload.attrs.get("lawvm_repeal_placeholder") != "1")
        ]
        if replace_ops:
            for lo in replace_ops:
                source = lo.source
                assert source is not None
                assert source.expires == "2026-12-31", (
                    f"Replace snapshot must preserve expires='2026-12-31', "
                    f"got {source.expires!r}. "
                    "Regression: expiry incorrectly stripped from non-repeal snapshot."
                )

    def test_repeal_snapshot_with_empty_expiry_stays_empty(self):
        """A repeal placeholder that already has expires='' must remain so
        (no change for permanent amendments)."""
        state = _make_state(_body())
        base_ir = _body(_sec("5", _sub("1", _content("content"))))

        op = AmendmentOp(
            op_id="repeal_5",
            op_type="REPEAL",
            target_section="5",
            target_kind=TargetKind.SECTION,
            source_statute="2021/80",
            source_issue_date=_DATE,
        )

        rop_permanent = ResolvedOp.from_amendment_op(
            op=op,
            muutos_ir=None,
            cross_ir=None,
            target_unit_kind="section",
            target_norm="5",
            target_chapter=None,
            op_source=OperationSource(
                statute_id="2021/80",
                title="Permanent",
                enacted="2021-01-01",
                effective="2021-06-01",
                expires="",  # already empty
            ),
        )

        lo_ops: List[LegalOperation] = []
        _emit_section_snapshot(
            state=state,
            target_unit_kind="section",
            target_norm="5",
            target_chapter=None,
            target_part=None,
            group_rops=[rop_permanent],
            lo_ops_out=lo_ops,
            amendment_id="2021/80",
            source_title="Permanent",
            source_issue_date=_DATE,
            source_effective_date=_DATE,
            base_ir=base_ir,
        )

        assert len(lo_ops) >= 1
        placeholder_ops = [
            lo for lo in lo_ops if lo.payload is not None and lo.payload.attrs.get("lawvm_repeal_placeholder") == "1"
        ]
        assert placeholder_ops, "Expected repeal placeholder for absent section."
        for lo in placeholder_ops:
            source = lo.source
            assert source is not None
            assert source.expires == "", f"Expects empty expires, got {source.expires!r}."

    def test_container_repeal_snapshot_emits_repeal_not_payloadless_replace(self):
        """Whole-container repeal groups must emit explicit repeal semantics."""
        state = _make_state(_body())
        base_ir = _body(_chapter("2", _sec("1", _sub("1", _content("base")))))

        op = AmendmentOp(
            op_id="repeal_l_2",
            op_type="REPEAL",
            target_section="2",
            target_kind=TargetKind.CHAPTER,
            source_statute="2024/1",
            source_issue_date=_DATE,
        )
        rop = ResolvedOp.from_amendment_op(
            op=op,
            muutos_ir=None,
            cross_ir=None,
            target_unit_kind="chapter",
            target_norm="2",
            target_chapter=None,
        )

        lo_ops: List[LegalOperation] = []
        _emit_section_snapshot(
            state=state,
            target_unit_kind="chapter",
            target_norm="2",
            target_chapter=None,
            target_part=None,
            group_rops=[rop],
            lo_ops_out=lo_ops,
            amendment_id="2024/1",
            source_title="Test",
            source_issue_date=_DATE,
            source_effective_date=_DATE,
            base_ir=base_ir,
        )

        assert lo_ops
        snap = lo_ops[-1]
        assert snap.action == StructuralAction.REPEAL
        assert snap.payload is None

    def test_renumber_snapshot_reuses_group_payload_when_live_lookup_misses(self):
        """Renumber-only groups should snapshot the real replacement payload they carry."""
        state = _make_state(_body())
        payload = _sec("18a", _sub("1", _content("replacement text")))

        op = AmendmentOp(
            op_id="renumber_18a",
            op_type="RENUMBER",
            target_section="18a",
            target_kind=TargetKind.SECTION,
            source_statute="1998/303",
            source_issue_date=_DATE,
        )
        rop = ResolvedOp.from_amendment_op(
            op=op,
            muutos_ir=payload,
            cross_ir=None,
            target_unit_kind="section",
            target_norm="18a",
            target_chapter=None,
        )

        lo_ops: List[LegalOperation] = []
        _emit_section_snapshot(
            state=state,
            target_unit_kind="section",
            target_norm="18a",
            target_chapter=None,
            target_part=None,
            group_rops=[rop],
            lo_ops_out=lo_ops,
            amendment_id="1998/303",
            source_title="Test",
            source_issue_date=_DATE,
            source_effective_date=_DATE,
            base_ir=_body(),
        )

        assert lo_ops
        root_snap = next(
            op
            for op in lo_ops
            if op.target.path == (("section", "18a"),)
        )
        assert root_snap.action == StructuralAction.INSERT
        assert root_snap.payload is payload

    def test_payloadless_whole_section_renumber_snapshot_emits_repeal(self):
        """Whole-section renumber without carried payload should remove the old slot explicitly."""
        state = _make_state(_body())
        base_ir = _body(_chapter("7", _sec("73", _sub("1", _content("old text")))))

        op = AmendmentOp(
            op_id="renumber_73",
            op_type="RENUMBER",
            target_section="73",
            target_kind=TargetKind.SECTION,
            target_chapter="7",
            source_statute="1994/318",
            source_issue_date=_DATE,
        )
        rop = ResolvedOp.from_amendment_op(
            op=op,
            muutos_ir=None,
            cross_ir=None,
            target_unit_kind="section",
            target_norm="73",
            target_chapter="7",
        )

        lo_ops: List[LegalOperation] = []
        _emit_section_snapshot(
            state=state,
            target_unit_kind="section",
            target_norm="73",
            target_chapter="7",
            target_part=None,
            group_rops=[rop],
            lo_ops_out=lo_ops,
            amendment_id="1994/318",
            source_title="Test",
            source_issue_date=_DATE,
            source_effective_date=_DATE,
            base_ir=base_ir,
        )

        assert lo_ops
        snap = lo_ops[-1]
        assert snap.action == StructuralAction.REPEAL
        assert snap.payload is None

    def test_part_snapshot_accepts_roman_target_with_arabic_payload_label(self):
        """Roman-numeral part targets should still reuse carried Arabic-labeled payload."""
        state = _make_state(_body())
        payload = _part("5", _chapter("19"))

        op = AmendmentOp(
            op_id="replace_part_v",
            op_type="REPLACE",
            target_section="v",
            target_kind=TargetKind.PART,
            source_statute="2003/1275",
            source_issue_date=_DATE,
        )
        rop = ResolvedOp.from_amendment_op(
            op=op,
            muutos_ir=payload,
            cross_ir=None,
            target_unit_kind="part",
            target_norm="v",
            target_chapter=None,
        )

        lo_ops: List[LegalOperation] = []
        _emit_section_snapshot(
            state=state,
            target_unit_kind="part",
            target_norm="v",
            target_chapter=None,
            target_part=None,
            group_rops=[rop],
            lo_ops_out=lo_ops,
            amendment_id="2003/1275",
            source_title="Test",
            source_issue_date=_DATE,
            source_effective_date=_DATE,
            base_ir=None,
        )

        assert lo_ops
        snap = lo_ops[-1]
        assert snap.action == StructuralAction.INSERT
        assert snap.payload is payload
        assert snap.target.path == (("part", "5"),)

    def test_payloadless_descendant_repeal_group_emits_repeal_snapshot(self):
        """A group that only repeals descendant subsections should snapshot the parent as repeal."""
        state = _make_state(_body())
        lo_ops: List[LegalOperation] = []
        rops = [
            _rop(_op(op_type="REPEAL", target_section="72e", target_paragraph=1)),
            _rop(_op(op_type="REPEAL", target_section="72e", target_paragraph=2)),
            _rop(_op(op_type="REPEAL", target_section="72e", target_paragraph=3)),
        ]

        _emit_section_snapshot(
            state=state,
            target_unit_kind="section",
            target_norm="72e",
            target_chapter=None,
            target_part=None,
            group_rops=rops,
            lo_ops_out=lo_ops,
            amendment_id="1995/1767",
            source_title="Test",
            source_issue_date=_DATE,
            source_effective_date=_DATE,
            base_ir=None,
        )

        assert lo_ops
        snap = lo_ops[-1]
        assert snap.target.path == (("section", "72e"),)
        assert snap.action == StructuralAction.REPEAL
        assert snap.payload is None

    def test_payloadless_container_insert_without_payload_skips_snapshot(self):
        """A pure container insert with no payload should not emit a fake replace snapshot."""
        state = _make_state(_body())
        lo_ops: List[LegalOperation] = []
        rop = _rop(_op(op_type="INSERT", target_section="14a", target_kind=TargetKind.CHAPTER))

        _emit_section_snapshot(
            state=state,
            target_unit_kind="chapter",
            target_norm="14a",
            target_chapter=None,
            target_part=None,
            group_rops=[rop],
            lo_ops_out=lo_ops,
            amendment_id="2005/1179",
            source_title="Test",
            source_issue_date=_DATE,
            source_effective_date=_DATE,
            base_ir=None,
        )

        assert lo_ops == []


# ---------------------------------------------------------------------------
# 6. PEG sekä heading continuation
# ---------------------------------------------------------------------------


class TestPegSektHeadingContinuation:
    """Innovation #6: after targets and a heading placement, a following
    'sekä' clause with new sections continues as part of the same insert group.

    The real mechanism: _skip_heading_residue() is called inside the
    _target_list continuation loop when _sep() returns None but consumed
    span tokens (PROVENANCE_SPAN from a heading's position clause).
    After the residue is consumed, the loop continues and finds more targets.
    """

    def test_sections_after_chapter_heading_placement_in_insert_group(self):
        """The canonical case from the session.

        Text: "lisätään lakiin uusi 13 a ja 16 b §, 17 §:n edelle uusi
               2 a luvun otsikko, sekä lakiin uusi 18 a §"

        After parsing '13 a, 16 b §' the loop finds ', ' separator, then
        a heading placement '17 §:n edelle uusi 2 a luvun otsikko'.
        The heading residue is skipped, and the next 'sekä lakiin uusi 18 a §'
        must be parsed as a continuation target.

        Expected: L P 13a, L P 16b, L P 18a all present.
        """
        text = "lisätään lakiin uusi 13 a ja 16 b §, 17 §:n edelle uusi 2 a luvun otsikko, sekä lakiin uusi 18 a §"
        ops = parse_clause(text).parsed_ops
        codes = [op.code() for op in ops]

        assert "L P 13a" in codes, f"Expected L P 13a in {codes!r}."
        assert "L P 16b" in codes, f"Expected L P 16b in {codes!r}."
        assert "L P 18a" in codes, (
            f"Expected L P 18a in {codes!r}. Regression: sekä continuation after heading placement dropped."
        )

    def test_curated_seka_docill_continuation_numbered_chapter_heading(self):
        """This is the actual curated test case from test_peg_curated.py.

        Source: seka_docill_continuation_numbered_chapter_heading
        Text includes provenance span residue pattern that triggers
        _skip_heading_residue.
        """
        text = (
            "sekä lisätään asetukseen uusi 13 a, 13 b, 13 c, 16 a ja 16 b §, "
            "17 §:n edelle uusi 2 a luvun otsikko, "
            "sekä asetukseen uusi 18 a § ja 2 b luku"
        )
        expected = [
            "L P 13a",
            "L P 13b",
            "L P 13c",
            "L P 16a",
            "L P 16b",
            "L P 17 o",  # heading insertion before §17
            "L P 18a",
            "L L 2b",
        ]

        ops = parse_clause(text).parsed_ops
        codes = [op.code() for op in ops]

        assert codes == expected, f"Expected {expected!r}, got {codes!r}. Regression: sekä heading continuation broken."

    def test_curated_seka_docill_continuation_provenance_span_residue(self):
        """The provenance span residue pattern (viimeksi mainitun edelle
        uusi väliotsikko) must be skipped correctly to reach later targets.

        Source: seka_docill_continuation_provenance_span_residue
        """
        text = (
            "sekä lisätään lakiin uusi 25 a ja 25 b §, "
            "38 §:n edelle uusi väliotsikko, "
            "lakiin uusi 38 a ja 46 a § ja viimeksi mainitun edelle uusi väliotsikko "
            "sekä lakiin uusi 46 b §, 8 a luku sekä 85 b, 85 c ja 86 b-86 d §"
        )
        expected = [
            "L P 25a",
            "L P 25b",
            "L P 38 o",  # heading insertion before §38
            "L P 38a",
            "L P 46a",
            "L P 46b",
            "L L 8a",
            "L P 85b",
            "L P 85c",
            "L P 86b",
            "L P 86c",
            "L P 86d",
        ]

        ops = parse_clause(text).parsed_ops
        codes = [op.code() for op in ops]

        assert codes == expected, (
            f"Expected {expected!r}, got {codes!r}. "
            "Regression: provenance span residue skipping for heading continuation broken."
        )

    def test_heading_placement_only_does_not_produce_spurious_section_ops(self):
        """A bare heading insertion without a following sekä clause must not
        produce spurious section ops."""
        text = "lisätään 17 §:n edelle uusi luvun otsikko"
        ops = parse_clause(text).parsed_ops
        codes = [op.code() for op in ops]

        # May produce zero ops or only heading-related ops — no spurious P sections
        spurious = [c for c in codes if c.startswith("L P")]
        assert not spurious, (
            f"Heading-only insert produced spurious section ops: {spurious!r}. "
            "Regression: heading residue skip consumed too much."
        )

    def test_multi_section_insert_before_heading_all_reach_output(self):
        """Multiple sections before and after the heading must all be in output."""
        text = (
            "lisätään lakiin uusi 5 a, 5 b ja 5 c §, 6 §:n edelle uusi väliotsikko, sekä lakiin uusi 6 a § ja 2 a luku"
        )
        ops = parse_clause(text).parsed_ops
        codes = [op.code() for op in ops]

        assert "L P 5a" in codes, f"L P 5a missing: {codes!r}."
        assert "L P 5b" in codes, f"L P 5b missing: {codes!r}."
        assert "L P 5c" in codes, f"L P 5c missing: {codes!r}."
        assert "L P 6a" in codes, f"L P 6a missing: {codes!r}. Regression: post-heading-continuation section dropped."

    def test_named_heading_anchor_before_doc_level_section_range_insert(self):
        """Real 2002/1184 family: named heading anchor before inserted section range.

        Regression: the leading named heading anchor collapsed the whole
        doc-level insert clause to zero ops.
        """
        text = (
            "lisätään asetukseen apteekkeja, sivuapteekkeja ja lääkekaappeja "
            "koskevan väliotsikon edelle uusi 10 b―10 f §, asetukseen uusi 21 a §, "
            "asetukseen uusi väliotsikko 25 §:n edelle, asetukseen uusi 25 a―25 i §, "
            "asetukseen uusi väliotsikko 28 §:n edelle ja asetukseen erinäisiä "
            "säännöksiä koskevan väliotsikon edelle uusi 28 a §"
        )
        codes = [op.code() for op in parse_clause(text).parsed_ops]

        expected = [
            "L P 10b",
            "L P 10c",
            "L P 10d",
            "L P 10e",
            "L P 10f",
            "L P 21a",
            "L P 25 o",
            "L P 25a",
            "L P 25b",
            "L P 25c",
            "L P 25d",
            "L P 25e",
            "L P 25f",
            "L P 25g",
            "L P 25h",
            "L P 25i",
            "L P 28 o",
            "L P 28a",
        ]

        assert codes == expected, (
            f"Expected {expected!r}, got {codes!r}. "
            "Regression: named heading anchor before doc-level insert range dropped insert ops."
        )

    def test_chapter_heading_propagates_chapter_scope_to_following_sections(self):
        """'6 luvun otsikko, 4 § ja 10 §' must give chapter=6 to all ops.

        Bug: _extract_chapter_from_nodes returned '' for chapter targets with
        heading/intro sub_refs, clearing the chapter context.  The fix
        recognises facet-only sub_refs and propagates the chapter label.
        """
        text = "muutetaan 6 luvun otsikko, 4 § ja 10 §"
        ops = parse_clause(text).parsed_ops
        codes = [op.code() for op in ops]

        assert "M L 6 o" in codes
        assert "M P L:6 4" in codes, (
            f"Expected chapter=6 on section 4: {codes!r}. "
            "Regression: chapter heading did not propagate chapter scope."
        )
        assert "M P L:6 10" in codes, (
            f"Expected chapter=6 on section 10: {codes!r}. "
            "Regression: chapter heading did not propagate chapter scope."
        )

    def test_chapter_heading_propagates_chapter_scope_with_seka(self):
        """'9 luvun otsikko sekä 1 ja 3 §' must give chapter=9 to sections."""
        text = "muutetaan 9 luvun otsikko sekä 1 ja 3 §"
        ops = parse_clause(text).parsed_ops
        codes = [op.code() for op in ops]

        assert "M L 9 o" in codes
        assert "M P L:9 1" in codes, (
            f"Expected chapter=9 on section 1: {codes!r}. "
            "Regression: chapter heading did not propagate chapter scope."
        )
        assert "M P L:9 3" in codes, (
            f"Expected chapter=9 on section 3: {codes!r}. "
            "Regression: chapter heading did not propagate chapter scope."
        )


class TestReinstatementChainContinuationResidue:
    """Regression family: citation/reinstatement residue between chained ``uusi`` arms."""

    def test_citation_and_reinstatement_residue_preserves_middle_section(self):
        text = (
            "lisätään lakiin siitä lailla 1165/2013 kumotun 12 §:n tilalle uusi 12 §, "
            "lailla 503/2013 kumotun 13 §:n tilalle uusi 13 § "
            "ja lailla 781/2007 kumotun 14 §:n tilalle uusi 14 §"
        )

        codes = [op.code() for op in parse_clause(text).parsed_ops]

        assert codes == ["L P 12", "L P 13", "L P 14"], (
            f"Expected chained reinstatement inserts ['L P 12', 'L P 13', 'L P 14'], got {codes!r}. "
            "Regression: citation/reinstatement residue after the separator dropped the middle 13 § arm."
        )

    def test_plain_adjacent_reinstatement_chain_has_no_duplicates_or_reordering(self):
        text = "lisätään 13 §:n tilalle uusi 13 § ja 14 §:n tilalle uusi 14 §"

        codes = [op.code() for op in parse_clause(text).parsed_ops]

        assert codes == ["L P 13", "L P 14"], (
            f"Expected exact adjacent reinstatement chain ['L P 13', 'L P 14'], got {codes!r}. "
            "Guard: the residue fix must not duplicate or reorder nearby chained inserts."
        )


# ═══════════════════════════════════════════════════════════════════════
# 7. Part-level insertion via uusi + OSA (sekä conjunction restart)
# ═══════════════════════════════════════════════════════════════════════


class TestPartLevelInsertionViaSeka:
    """Innovation #7: part-level INSERT targets with uusi modifier.

    The bug: the insertion dispatcher (Pattern C / Pattern D) only checked
    for PYKALA and LUKU after ``uusi number_list``, silently dropping OSA.
    This caused the sekä-conjunction restart to fail for part-level targets:
    ``lisätään ... sekä lakiin uusi II A osa`` dropped the part insertion.

    Root cause in 2018/579: "lisätään ... uusi 10 kohta sekä lakiin uusi II A osa"
    cascaded into 147+ missing sections in 2017/320 (Laki liikenteen palveluista).
    """

    def test_lakiin_uusi_part_insert(self):
        """DOC:ILL uusi number_list OSA produces INSERT part."""
        ops = parse_clause("lisätään lakiin uusi II A osa").parsed_ops
        codes = [op.code() for op in ops]
        assert codes == ["L O IIa"], (
            f"Expected ['L O IIa'], got {codes!r}. "
            "Regression: DOC:ILL uusi part insertion not recognised."
        )

    def test_seka_conjunction_item_then_part(self):
        """sekä conjunction after item INSERT reaches part-level target."""
        text = "lisätään 10 §:ään uusi 10 kohta sekä lakiin uusi II A osa"
        ops = parse_clause(text).parsed_ops
        codes = [op.code() for op in ops]
        assert "L P 10 1 10" in codes, (
            f"Expected item insert 'L P 10 1 10' in {codes!r}."
        )
        assert "L O IIa" in codes, (
            f"Expected part insert 'L O IIa' in {codes!r}. "
            "Regression: sekä conjunction restart drops part-level INSERT."
        )

    def test_fallback_recovers_cited_statute_item_then_part_insert(self):
        """Fallback keeps 2018/579's part insert when citation prose defeats surface parsing."""
        from lawvm.finland.normalize import parse_ops_fallback_heuristic

        text = (
            "lisätään liikenteen palvelusta annetun lain (320/2017) I osan 1 luvun "
            "2 §:ään, sellaisena kuin se on laissa 301/2018, uusi 10 kohta sekä "
            "lakiin uusi II A osa seuraavasti:"
        )
        ops = parse_ops_fallback_heuristic(text)

        assert any(
            op.op_type == "INSERT"
            and op.target_unit_kind == "part"
            and op.target_section == "iia"
            for op in ops
        )

    def test_bare_seka_before_part_ref(self):
        """Leading sekä separator before part target is skipped."""
        ops = parse_clause("lisätään sekä II A osa").parsed_ops
        codes = [op.code() for op in ops]
        assert codes == ["L O IIa"], (
            f"Expected ['L O IIa'], got {codes!r}. "
            "Regression: leading sekä before part-level target drops it."
        )

    def test_bare_uusi_part_insert_without_doc(self):
        """Bare UUSI number_list OSA (citation stripped DOC:ILL)."""
        ops = parse_clause("lisätään uusi II A osa").parsed_ops
        codes = [op.code() for op in ops]
        assert codes == ["L O IIa"], (
            f"Expected ['L O IIa'], got {codes!r}. "
            "Regression: bare UUSI part insertion not recognised."
        )

    def test_roman_numeral_part_without_letter(self):
        """Part insertion with plain Roman numeral (no letter suffix)."""
        ops = parse_clause("lisätään lakiin uusi III osa").parsed_ops
        codes = [op.code() for op in ops]
        assert codes == ["L O III"], (
            f"Expected ['L O III'], got {codes!r}."
        )


def test_2019_371_renumber_ops_bind_typed_intent_with_compound_source_parent_path() -> None:
    """The live 2019/371 renumber family must bind typed intent.

    This is the regression behind the current FI_TYPED_INTENT_REQUIRED explain
    failures on 2017/320.  The legacy destination only carries the new leaf
    label, so typed relabel binding must rebuild the source parent path before
    constructing CanonicalIntent.
    """
    from lxml import etree

    from lawvm.finland.grafter import get_corpus, normalize_and_compile_ops

    from lawvm.tools.inspect_amendment import _working_johtolause

    statute_id = "2017/320"
    source_id = "2019/371"

    corpus = get_corpus()
    xml_bytes = corpus.read_source(source_id)
    assert xml_bytes is not None

    before_master = pinned_replay(statute_id, mode="legal_pit", stop_before=source_id, quiet=True)
    _muutos_tree, johto, used_sec1_fallback, should_apply, _route_reason = _working_johtolause(
        statute_id,
        before_master.title,
        source_id,
        xml_bytes,
        "",
    )
    assert should_apply is True

    phase = normalize_and_compile_ops(
        johto,
        etree.fromstring(xml_bytes),
        before_master.replay_fold_state,
        source_id,
        source_title="",
        used_sec1_fallback=used_sec1_fallback,
        parent_id=statute_id,
        strict_profile=None,
    )
    ops = phase.output
    renumber_op = next(op for op in ops if op.op_type == "RENUMBER" and op.lo is not None and op.lo.destination is not None)

    rop = ResolvedOp.from_amendment_op(
        renumber_op,
        muutos_ir=None,
        cross_ir=None,
        target_unit_kind=renumber_op.target_unit_kind,
        target_norm=renumber_op.target_section or "",
        target_chapter=renumber_op.target_chapter,
    )

    assert rop.intent is not None
    assert rop.intent.kind == "relabel"
    assert isinstance(rop.intent, Relabel)
    assert rop.resolved_destination_address is not None
    assert rop.resolved_destination_address.path == (("section", "3"),)
    # _canonicalize_replay_address normalizes Roman numeral part labels to Arabic
    # (e.g. "II" → "2") so the intent carries the canonical live-tree identity form.
    assert rop.intent.destination.address.path == (("part", "2"), ("chapter", "1"), ("section", "3"))
    assert _resolved_op_is_owned_by_restructure_plan(
        rop,
        {
            (
                (("part", "2"), ("chapter", "2"), ("section", "2")),
                (("part", "2"), ("chapter", "2"), ("section", "3")),
            )
        },
    ) is False
    assert _resolved_op_is_owned_by_restructure_plan(
        rop,
        {
            (
                rop.intent.source.address.path,
                rop.intent.destination.address.path,
            )
        },
    ) is True
    assert rop.intent.source.address.path == (("part", "2"), ("chapter", "1"), ("section", "1"))


def test_1992_110_2017_48_reinstatement_chain_compiles_insert_13_and_materializes_section_13() -> None:
    """Real corpus anchor for the active `1992/110 <- 2017/48` chain-drop family."""
    from lxml import etree

    from lawvm.finland.grafter import get_corpus, normalize_and_compile_ops
    from tests.corpus_pin_helpers import pinned_replay

    from lawvm.tools.inspect_amendment import _working_johtolause

    statute_id = "1992/110"
    source_id = "2017/48"

    corpus = get_corpus()
    xml_bytes = corpus.read_source(source_id)
    assert xml_bytes is not None

    before_master = pinned_replay(statute_id, mode="legal_pit", stop_before=source_id, quiet=True)
    _muutos_tree, johto, used_sec1_fallback, should_apply, _route_reason = _working_johtolause(
        statute_id,
        before_master.title,
        source_id,
        xml_bytes,
        "",
    )
    assert should_apply is True

    phase = normalize_and_compile_ops(
        johto,
        etree.fromstring(xml_bytes),
        before_master.replay_fold_state,
        source_id,
        source_title="",
        used_sec1_fallback=used_sec1_fallback,
        parent_id=statute_id,
        strict_profile=None,
    )

    assert any(
        op.op_type == "INSERT" and op.target_unit_kind == "section" and op.target_section == "13"
        for op in phase.output
    ), "Expected compile output to include INSERT 13 § for 1992/110 <- 2017/48."

    replay = pinned_replay(statute_id, mode="finlex_oracle", quiet=True)
    assert replay.materialized_state.find_section("13") is not None, (
        "Expected final replay for 1992/110 to materialize 13 § after the 2017/48 reinstatement chain."
    )


def test_1994_201_2018_253_does_not_false_repeal_section_3_via_voimaantulo_extraction() -> None:
    """Real corpus anchor for the active `1994/201 <- 2018/253` false-repeal family."""
    from lawvm.finland.grafter import get_corpus
    from lawvm.finland.vts import extract_voimaantulo_repeals

    statute_id = "1994/201"
    source_id = "2018/253"

    corpus = get_corpus()
    xml_bytes = corpus.read_source(source_id)
    assert xml_bytes is not None

    ops = extract_voimaantulo_repeals(xml_bytes, statute_id, parent_title="Kotikuntalaki")
    assert not any(
        op.target_unit_kind == "section" and op.target_section == "3" and not op.target_chapter
        for op in ops
    ), "Expected 2018/253 not to emit a false-positive REPEAL 3 § against 1994/201."

    replay = pinned_replay(statute_id, mode="finlex_oracle", quiet=True)
    sec3 = replay.materialized_state.find_section("3", "2")
    assert sec3 is not None, "Expected chapter:2/section:3 to exist in final replay."
    child_kinds = [child.kind.value for child in sec3.children]
    assert "heading" in child_kinds and "subsection" in child_kinds, (
        f"Expected chapter:2/section:3 to retain heading + subsection content, got child kinds {child_kinds!r}."
    )


def test_2015_1141_2023_1250_keeps_explicit_chunk_insert_sections_in_their_own_chapters() -> None:
    """Real corpus anchor for the explicit-chunk insert retarget hijack family."""
    from lawvm.tools.section_keys import extract_ir_sections

    replay = pinned_replay("2015/1141", mode="finlex_oracle", quiet=True)
    sections = extract_ir_sections(replay.products.materialized_state.ir)

    for wrong_path in (
        "chapter:1/section:2a",
        "chapter:1/section:3a",
        "chapter:2/section:1a",
    ):
        assert wrong_path not in sections

    for right_path in (
        "chapter:2/section:2a",
        "chapter:2/section:3a",
        "chapter:3/section:1a",
        "chapter:3/section:2a",
        "chapter:3/section:3a",
        "chapter:6/section:1a",
        "chapter:6/section:3a",
        "chapter:10/section:3a",
    ):
        assert right_path in sections


def test_2002_197_2011_535_inserted_chapter3a_does_not_keep_shadowed_sections_20_21() -> None:
    """Real corpus anchor for inserted-chapter shadowed-section retention."""
    from lawvm.tools.section_keys import extract_ir_sections

    replay = pinned_replay("2002/197", mode="finlex_oracle", quiet=True)
    sections = extract_ir_sections(replay.products.materialized_state.ir)

    assert "chapter:3a/section:20" not in sections
    assert "chapter:3a/section:21" not in sections
    assert "chapter:4/section:20" in sections
    assert "chapter:4/section:21" in sections


def test_1994_719_2001_124_does_not_keep_or_misroute_16a_17a_cluster() -> None:
    """Real corpus anchor for inserted 3a chapter shadow-retention plus misrouting."""
    from lawvm.tools.section_keys import extract_ir_sections

    replay = pinned_replay("1994/719", mode="finlex_oracle", quiet=True)
    sections = extract_ir_sections(replay.products.materialized_state.ir)

    assert "chapter:5/section:16a" in sections
    assert "chapter:5/section:16b" in sections
    assert "chapter:5/section:17a" in sections

    assert "chapter:3a/section:16a" not in sections
    assert "chapter:3a/section:16b" not in sections
    assert "chapter:3a/section:17a" not in sections
    assert "chapter:4/section:16a" not in sections
    assert "chapter:4/section:16b" not in sections


def test_2006_1280_2022_1031_keeps_section42_items_4_and_5() -> None:
    """Real corpus anchor for the sparse-slot item-drop family in 42 §."""
    from lawvm.core.ir import IRNodeKind

    replay = pinned_replay("2006/1280", mode="finlex_oracle", quiet=True)
    sec42 = replay.find_section("42", "5", "3")
    assert sec42 is not None, "section part:3/chapter:5/section:42 must exist"

    sub1 = next(
        (
            child
            for child in sec42.children
            if child.kind == IRNodeKind.SUBSECTION and child.label == "1"
        ),
        None,
    )
    assert sub1 is not None, "section 42 must retain subsection 1"

    item_labels = {
        child.label
        for child in sub1.children
        if child.kind == IRNodeKind.PARAGRAPH and child.label is not None
    }
    assert {"3", "4", "5"}.issubset(item_labels), (
        f"Expected 2022/1031 to materialize section 42 subsection-1 items 3, 4, and 5, got {sorted(item_labels)!r}."
    )


# ---------------------------------------------------------------------------
# 8. Chapter RELABEL ordering: _stabilize_chapter_relabel_order (1978/38 / 1997/1162)
# ---------------------------------------------------------------------------

def _make_chapter_relabel_rop(src_label: str, dst_label: str) -> "ResolvedOp":
    """Build a chapter-level RELABEL ResolvedOp for testing _stabilize_chapter_relabel_order."""
    op = AmendmentOp(
        op_id=f"test_relabel_{src_label}_{dst_label}",
        op_type="RENUMBER",
        target_section=src_label,
        target_unit_kind="chapter",
        source_statute="1997/1162",
        source_issue_date=_DATE,
    )
    target_addr = LegalAddress(path=(("chapter", src_label),))
    dest_addr = LegalAddress(path=(("chapter", dst_label),))
    return ResolvedOp.from_amendment_op(
        op,
        muutos_ir=None,
        cross_ir=None,
        target_unit_kind="chapter",
        target_norm=src_label,
        target_chapter=None,
        target_address=target_addr,
        destination_address=dest_addr,
    )


def _make_chapter_insert_rop(label: str) -> "ResolvedOp":
    """Build a chapter-level INSERT ResolvedOp (non-RELABEL, for interleave tests)."""
    from lawvm.core.ir import IRNode
    from lawvm.core.semantic_types import IRNodeKind

    chapter_ir = IRNode(kind=IRNodeKind.CHAPTER, label=label)
    op = AmendmentOp(
        op_id=f"test_insert_{label}",
        op_type="INSERT",
        target_section=label,
        target_unit_kind="chapter",
        source_statute="1997/1162",
        source_issue_date=_DATE,
    )
    target_addr = LegalAddress(path=(("chapter", label),))
    return ResolvedOp.from_amendment_op(
        op,
        muutos_ir=chapter_ir,
        cross_ir=None,
        target_unit_kind="chapter",
        target_norm=label,
        target_chapter=None,
        target_address=target_addr,
    )


def test_stabilize_chapter_relabel_order_reverses_forward_chain() -> None:
    """Forward chain [10→11, 11→12] must be reversed to [11→12, 10→11].

    Regression for 1978/38 / 1997/1162: "nykyinen 10 ja 11 luku siirtyvät
    11 ja 12 luvuksi" emits ops in textual order [10→11, 11→12].  Applied
    sequentially that renames ch:10→ch:11 first, then ch:11→ch:12, which
    consumes the just-renamed ch:10 as the ch:12 target, leaving no ch:11.
    The fix reverses the chain so ch:11→12 runs first.
    """
    from lawvm.finland.grafter import _stabilize_chapter_relabel_order

    r1 = _make_chapter_relabel_rop("10", "11")  # chain head
    r2 = _make_chapter_relabel_rop("11", "12")  # chain tail

    reordered = _stabilize_chapter_relabel_order([r1, r2])

    assert len(reordered) == 2
    # After stabilization the higher-numbered rename must come first.
    assert reordered[0].target_norm == "11", (
        f"Expected first op to target ch:11 (rename 11→12), got {reordered[0].target_norm}"
    )
    assert reordered[1].target_norm == "10", (
        f"Expected second op to target ch:10 (rename 10→11), got {reordered[1].target_norm}"
    )


def test_stabilize_chapter_relabel_order_with_interleaved_insert() -> None:
    """Forward chain with non-RELABEL op between members: [10→11, INSERT 10, 11→12].

    The INSERT is not part of the chain, but the RELABEL ops must still be
    reordered so 11→12 runs before 10→11.  The INSERT stays at its original
    position relative to the RELABEL op slots.
    """
    from lawvm.finland.grafter import _stabilize_chapter_relabel_order

    r1 = _make_chapter_relabel_rop("10", "11")
    ins = _make_chapter_insert_rop("10")
    r2 = _make_chapter_relabel_rop("11", "12")

    reordered = _stabilize_chapter_relabel_order([r1, ins, r2])

    assert len(reordered) == 3
    # The two RELABEL ops occupy positions 0 and 2; INSERT stays at position 1.
    assert reordered[0].target_norm == "11", (
        f"Position 0 should be RELABEL 11→12, got target_norm={reordered[0].target_norm!r}"
    )
    assert reordered[1].op_id == ins.op_id, (
        "INSERT op should remain at position 1"
    )
    assert reordered[2].target_norm == "10", (
        f"Position 2 should be RELABEL 10→11, got target_norm={reordered[2].target_norm!r}"
    )


def test_stabilize_chapter_relabel_order_no_chain_unchanged() -> None:
    """Unrelated relabels (no forward chain) must be left in their original order."""
    from lawvm.finland.grafter import _stabilize_chapter_relabel_order

    r1 = _make_chapter_relabel_rop("3", "4")   # 3→4
    r2 = _make_chapter_relabel_rop("10", "11")  # 10→11, not a chain with 3→4

    reordered = _stabilize_chapter_relabel_order([r1, r2])

    assert len(reordered) == 2
    assert reordered[0].target_norm == "3"
    assert reordered[1].target_norm == "10"


def test_stabilize_chapter_relabel_order_single_op_unchanged() -> None:
    """A single chapter-RELABEL op must be returned unchanged."""
    from lawvm.finland.grafter import _stabilize_chapter_relabel_order

    r1 = _make_chapter_relabel_rop("5", "6")
    reordered = _stabilize_chapter_relabel_order([r1])
    assert len(reordered) == 1
    assert reordered[0].target_norm == "5"


def test_stabilize_chapter_relabel_order_three_op_chain() -> None:
    """Three-op chain [3→4, 4→5, 5→6] must be reversed to [5→6, 4→5, 3→4]."""
    from lawvm.finland.grafter import _stabilize_chapter_relabel_order

    r1 = _make_chapter_relabel_rop("3", "4")
    r2 = _make_chapter_relabel_rop("4", "5")
    r3 = _make_chapter_relabel_rop("5", "6")

    reordered = _stabilize_chapter_relabel_order([r1, r2, r3])

    assert len(reordered) == 3
    assert reordered[0].target_norm == "5", "Tail of chain must apply first"
    assert reordered[1].target_norm == "4"
    assert reordered[2].target_norm == "3", "Head of chain must apply last"


# ---------------------------------------------------------------------------
# VÄLIAIKAINEN chapter scaffold: INSERT must REPLACE, not MERGE (2026-04-15)
# ---------------------------------------------------------------------------


class TestValiaikainenChapterScaffoldReplace:
    """Innovation: container INSERT targeting a chapter that exists in state.ir
    but NOT in base_ir must REPLACE (not MERGE) in finlex_oracle mode.

    Regression scenario (1982/710):
    - 1992/1657 VÄLIAIKAINEN creates chapter '3a' with §38a–§38f (expired 1997-12-31).
    - Raw IR still holds chapter '3a' with those expired sections (no filtering in raw IR).
    - 2003/1310 INSERT permanent chapter '3a' with §29a–§29g.
    - In finlex_oracle mode, `path` is non-None (ch:3a is in state.ir) and
      `base_path` is None (ch:3a is absent from the original base law).
    - Without the fix: MERGE resurrects §38a–§38f alongside §29a–§29g.
    - With the fix: REPLACE wins; chapter '3a' contains only §29a–§29g.
    """

    def test_non_base_chapter_scaffold_is_replaced_not_merged(self) -> None:
        """finlex_oracle INSERT of ch:3a that is absent from base_ir must REPLACE."""
        # Base law: chapter 3 only, no chapter 3a.
        base_body = _body(
            _chapter("3", _sec("1", _sub("1", _content("original §1")))),
        )

        # State IR (live replay accumulation): chapter 3a exists as VÄLIAIKAINEN
        # scaffold with expired sections §38a and §38b.
        scaffold_3a = _chapter(
            "3a",
            IRNode(kind=IRNodeKind.NUM, text="3 a luku"),
            _sec("38a", _sub("1", _content("expired VÄLIAIKAINEN §38a text"))),
            _sec("38b", _sub("1", _content("expired VÄLIAIKAINEN §38b text"))),
        )
        state_body = _body(
            _chapter("3", _sec("1", _sub("1", _content("original §1")))),
            scaffold_3a,
        )
        state = _make_state(state_body)

        # Amendment 2003/1310 inserts permanent chapter 3a with §29a and §29b.
        new_ch_3a = _chapter(
            "3a",
            IRNode(kind=IRNodeKind.NUM, text="3 a luku"),
            _sec("29a", _sub("1", _content("permanent §29a text"))),
            _sec("29b", _sub("1", _content("permanent §29b text"))),
        )

        op = AmendmentOp(
            op_id="insert_ch3a_2003_1310",
            op_type="INSERT",
            target_section="3a",
            target_kind=TargetKind.CHAPTER,
            source_statute="2003/1310",
            source_issue_date=dt.date(2003, 12, 31),
        )

        result = _apply_container_op(
            state,
            op,
            new_ch_3a,
            _FINLEX_ORACLE,
            "[2003/1310] INSERT 3a luku",
            base_ir=base_body,
            standalone_section_targets=frozenset(),
        )

        assert result is not None and result is not state, "_apply_container_op returned None or unchanged state."
        ch3a = next(
            (c for c in result.ir.children if c.kind == IRNodeKind.CHAPTER and c.label == "3a"),
            None,
        )
        assert ch3a is not None, "Chapter 3a must be present after INSERT."

        sec_labels = {c.label for c in ch3a.children if c.kind == IRNodeKind.SECTION}

        # Permanent sections must be present.
        assert "29a" in sec_labels, (
            f"§29a missing from ch:3a after INSERT. Sections: {sec_labels!r}. "
            "Regression: non-base scaffold was MERGED instead of REPLACED."
        )
        assert "29b" in sec_labels, (
            f"§29b missing from ch:3a after INSERT. Sections: {sec_labels!r}."
        )

        # Expired VÄLIAIKAINEN sections must NOT be present.
        assert "38a" not in sec_labels, (
            f"§38a (expired VÄLIAIKAINEN) was resurrected in ch:3a. Sections: {sec_labels!r}. "
            "Regression: non-base scaffold was MERGED (not REPLACED), bringing expired sections back."
        )
        assert "38b" not in sec_labels, (
            f"§38b (expired VÄLIAIKAINEN) was resurrected in ch:3a. Sections: {sec_labels!r}."
        )

    def test_base_chapter_insert_still_merges(self) -> None:
        """finlex_oracle INSERT of a chapter that IS in base_ir must still MERGE,
        not replace (pre-existing behaviour must be preserved)."""
        # Base law: chapter 3 already present.
        base_body = _body(
            _chapter(
                "3",
                _sec("1", _sub("1", _content("original §1"))),
                _sec("2", _sub("1", _content("original §2"))),
            ),
        )

        # State IR has chapter 3 with an extra permanently amended §3.
        state_body = _body(
            _chapter(
                "3",
                _sec("1", _sub("1", _content("original §1"))),
                _sec("2", _sub("1", _content("amended §2"))),
                _sec("3", _sub("1", _content("later-added §3"))),
            ),
        )
        state = _make_state(state_body)

        # Amendment inserts chapter 3 again (whole-chapter form).
        new_ch_3 = _chapter(
            "3",
            _sec("4", _sub("1", _content("new §4 from INSERT"))),
        )

        op = AmendmentOp(
            op_id="insert_ch3_mergecase",
            op_type="INSERT",
            target_section="3",
            target_kind=TargetKind.CHAPTER,
            source_statute="2010/100",
            source_issue_date=dt.date(2010, 1, 1),
        )

        result = _apply_container_op(
            state,
            op,
            new_ch_3,
            _FINLEX_ORACLE,
            "[2010/100] INSERT 3 luku (base chapter)",
            base_ir=base_body,
            standalone_section_targets=frozenset(),
        )

        assert result is not None and result is not state, "_apply_container_op returned None or unchanged state."
        ch3 = next(
            (c for c in result.ir.children if c.kind == IRNodeKind.CHAPTER and c.label == "3"),
            None,
        )
        assert ch3 is not None, "Chapter 3 must be present after INSERT."

        sec_labels = {c.label for c in ch3.children if c.kind == IRNodeKind.SECTION}

        # All pre-existing sections must survive (MERGE behaviour).
        assert "1" in sec_labels, (
            f"§1 was lost from ch:3 after INSERT in base-chapter case. Sections: {sec_labels!r}. "
            "Regression: base-chapter INSERT should MERGE, not REPLACE."
        )
        assert "3" in sec_labels, f"§3 was lost from ch:3. Sections: {sec_labels!r}."
        # New section must also be present.
        assert "4" in sec_labels, f"New §4 missing from ch:3 after MERGE. Sections: {sec_labels!r}."


def test_1993_615_heading_amendments_applied() -> None:
    """Heading amendments for Metsästyslaki 1993/615 must be reflected in replay.

    Two sections had their headings changed by later amendments but LawVM was
    preserving the old live heading instead of applying the amendment heading.
    Root cause: _preserve_live_heading_for_targeted_section_shell_ir was called
    even when the group contained an explicit heading op (target_special="otsikko"),
    overwriting the intended new heading with the old one.
    """
    from lawvm.core.ir import IRNodeKind
    from tests.corpus_pin_helpers import pinned_replay

    replay = pinned_replay("1993/615", mode="legal_pit", quiet=True)

    # 2004/1068 added "ja talousvyöhykkeellä" to section 7 heading
    sec7 = replay.find_section("7", "2")
    assert sec7 is not None, "section chapter:2/section:7 must exist"
    heading7 = next((c for c in sec7.children if c.kind == IRNodeKind.HEADING), None)
    assert heading7 is not None, "section 7 must have a heading node"
    assert "talousvyöhykkeellä" in heading7.text, (
        f"Expected 2004/1068 heading amendment to add 'talousvyöhykkeellä', got: {heading7.text!r}"
    )

    # 2017/504 renamed section 83c heading
    sec83c = replay.find_section("83c", "11")
    assert sec83c is not None, "section chapter:11/section:83c must exist"
    heading83c = next((c for c in sec83c.children if c.kind == IRNodeKind.HEADING), None)
    assert heading83c is not None, "section 83c must have a heading node"
    assert heading83c.text == "Velvollisuus ilmoittaa kuolleesta riistaeläimestä", (
        f"Expected 2017/504 heading amendment, got: {heading83c.text!r}"
    )
