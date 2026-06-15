"""Phase 3b Batch 1+2 — shadow-mode equivalence tests for SurfaceNode migration.

Validates that:
1. parse_to_surface(text) -> lower -> codes == extract_ops(text) -> codes
   (shadow-mode round-trip equivalence for ALL 125 curated cases)
2. Specific golden tests for section, chapter, part, nimike, appendix refs
3. Verb group structure is preserved correctly
4. Sub-references (momentti, kohta, otsikko, johd) round-trip correctly

Batch 2 additions:
5. Descendant consolidation: consecutive same-section ops are merged into
   a single SurfaceTargetRef with multiple sub_refs
6. Golden tests for each descendant pattern at surface-node level
"""

from __future__ import annotations

import pytest

from lawvm.core.semantic_types import FacetKind
from lawvm.finland.johtolause.api import parse_clause
from lawvm.finland.johtolause.curated_cases import CURATED_CASES
from lawvm.finland.johtolause.lift_to_surface import (
    lift_parsed_ops_to_surface_clause,
    parse_to_surface,
)
from lawvm.finland.johtolause.lower_surface import lower_surface_clause_to_parsed_ops
from lawvm.finland.johtolause.surface_model import (
    SurfaceDescendantCoordination,
    SurfaceCrossVerbMoveTail,
    SurfaceSubRef,
    SurfaceTargetRef,
    SurfaceMoveTail,
    TargetKind,
    VerbKind,
)
from lawvm.finland.johtolause.types import ParsedOp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _codes(ops: list[ParsedOp]) -> list[str]:
    """Extract op-code strings for easy comparison."""
    return [op.code() for op in ops]


def _shadow_compare(text: str) -> tuple[list[str], list[str]]:
    """Run both paths and return (original_codes, round_trip_codes).

    Path 1 (original): text -> parse_clause -> codes
    Path 2 (round-trip): text -> parse_to_surface -> lower -> codes
    """
    parser_result = parse_clause(text)
    original_ops = parser_result.parsed_ops
    original_codes = _codes(original_ops)

    surface = parse_to_surface(text)
    round_trip_ops = lower_surface_clause_to_parsed_ops(surface)
    round_trip_codes = _codes(round_trip_ops)

    return original_codes, round_trip_codes


class TestMoveTailValidation:
    def test_surface_move_tail_requires_destination(self) -> None:
        with pytest.raises(ValueError, match="requires a destination chapter or part"):
            SurfaceMoveTail()

    def test_surface_cross_verb_move_tail_requires_matching_destination_kind(self) -> None:
        with pytest.raises(ValueError, match="chapter move tails require destination_chapter"):
            SurfaceCrossVerbMoveTail(
                source_section_label="85b",
                destination_part="I",
                move_clause_target_unit_kind="chapter",
            )


# ===========================================================================
# Shadow-mode equivalence: ALL curated cases
# ===========================================================================


def _case_ids():
    return [tc["name"] for tc in CURATED_CASES]


@pytest.mark.parametrize("tc", CURATED_CASES, ids=_case_ids())
def test_shadow_mode_curated_case(tc):
    """Shadow-mode equivalence for each curated PEG test case.

    Proves the Phase 3 migration is behavior-preserving:
      extract_ops(text) -> codes == parse_to_surface(text) -> lower -> codes
    """
    if tc.get("xfail"):
        pytest.xfail("known failure in base parser")

    text = tc["text"]
    original_codes, round_trip_codes = _shadow_compare(text)

    assert round_trip_codes == original_codes, (
        f"\nInput:      {text[:120]}\nOriginal:   {original_codes}\nRound-trip: {round_trip_codes}"
    )


# ===========================================================================
# Golden tests: section refs
# ===========================================================================


class TestSectionRefGolden:
    """Golden tests for section reference lifting."""

    def test_simple_section(self):
        """'muutetaan 7 §' lifts to SurfaceTargetRef(kind=SECTION, label='7')."""
        clause = parse_to_surface("muutetaan 7 §")
        assert len(clause.verb_groups) == 1
        vg = clause.verb_groups[0]
        assert vg.verb == VerbKind.MUUTTAA
        assert len(vg.nodes) == 1
        node = vg.nodes[0]
        assert isinstance(node, SurfaceTargetRef)
        assert node.kind == TargetKind.SECTION
        assert node.label == "7"
        assert node.chapter == ""
        assert node.sub_refs == ()

    def test_section_with_chapter(self):
        """'muutetaan 3 luvun 12 §' lifts with chapter='3'."""
        clause = parse_to_surface("muutetaan 3 luvun 12 §")
        vg = clause.verb_groups[0]
        node = vg.nodes[0]
        assert isinstance(node, SurfaceTargetRef)
        assert node.kind == TargetKind.SECTION
        assert node.label == "12"
        assert node.chapter == "3"

    def test_section_with_momentti(self):
        """'muutetaan 7 §:n 2 momentti' lifts with sub_ref momentti=2."""
        clause = parse_to_surface("muutetaan 7 §:n 2 momentti")
        vg = clause.verb_groups[0]
        node = vg.nodes[0]
        assert isinstance(node, SurfaceTargetRef)
        assert node.label == "7"
        assert len(node.sub_refs) == 1
        assert node.sub_refs[0].momentti == 2

    def test_section_with_otsikko(self):
        """'muutetaan 6 §:n otsikko' lifts with sub_ref special='otsikko'."""
        clause = parse_to_surface("muutetaan 6 §:n otsikko")
        vg = clause.verb_groups[0]
        node = vg.nodes[0]
        assert isinstance(node, SurfaceTargetRef)
        assert node.label == "6"
        assert len(node.sub_refs) == 1
        assert node.sub_refs[0].special == "otsikko"

    def test_section_with_johd(self):
        """'muutetaan 15 §:n johdantokappale' lifts with special='johd'."""
        clause = parse_to_surface("muutetaan 15 §:n johdantokappale")
        vg = clause.verb_groups[0]
        node = vg.nodes[0]
        assert isinstance(node, SurfaceTargetRef)
        assert node.label == "15"
        assert len(node.sub_refs) == 1
        assert node.sub_refs[0].special == "johd"

    def test_section_with_kohta(self):
        """'muutetaan 5 §:n 1 momentin 3 kohta' lifts with momentti=1, item='3'."""
        clause = parse_to_surface("muutetaan 5 §:n 1 momentin 3 kohta")
        vg = clause.verb_groups[0]
        node = vg.nodes[0]
        assert isinstance(node, SurfaceTargetRef)
        assert node.label == "5"
        assert len(node.sub_refs) == 1
        assert node.sub_refs[0].momentti == 1
        assert node.sub_refs[0].item == "3"

    def test_section_letter_suffix(self):
        """'muutetaan 5 a §' lifts with label='5a'."""
        clause = parse_to_surface("muutetaan 5 a §")
        vg = clause.verb_groups[0]
        node = vg.nodes[0]
        assert isinstance(node, SurfaceTargetRef)
        assert node.label == "5a"

    def test_section_list_round_trip(self):
        """'muutetaan 3, 5 ja 7 §' produces three SurfaceTargetRef nodes."""
        clause = parse_to_surface("muutetaan 3, 5 ja 7 §")
        vg = clause.verb_groups[0]
        assert len(vg.nodes) == 3
        labels = [n.label for n in vg.nodes if isinstance(n, SurfaceTargetRef)]
        assert labels == ["3", "5", "7"]

    def test_section_range_round_trip(self):
        """'muutetaan 21-23 §' expands to three sections."""
        original, round_trip = _shadow_compare("muutetaan 21\u201323 §")
        assert round_trip == original == ["M P 21", "M P 22", "M P 23"]


# ===========================================================================
# Golden tests: chapter refs
# ===========================================================================


class TestChapterRefGolden:
    """Golden tests for chapter reference lifting."""

    def test_chapter_repeal(self):
        """'kumotaan 3 luku' lifts to SurfaceTargetRef(kind=CHAPTER, label='3')."""
        clause = parse_to_surface("kumotaan 3 luku")
        vg = clause.verb_groups[0]
        assert vg.verb == VerbKind.KUMOTA
        assert len(vg.nodes) == 1
        node = vg.nodes[0]
        assert isinstance(node, SurfaceTargetRef)
        assert node.kind == TargetKind.CHAPTER
        assert node.label == "3"
        assert node.sub_refs == ()

    def test_chapter_otsikko(self):
        """'muutetaan 5 luvun otsikko' lifts with otsikko sub_ref."""
        clause = parse_to_surface("muutetaan 5 luvun otsikko")
        vg = clause.verb_groups[0]
        node = vg.nodes[0]
        assert isinstance(node, SurfaceTargetRef)
        assert node.kind == TargetKind.CHAPTER
        assert node.label == "5"
        assert len(node.sub_refs) == 1
        assert node.sub_refs[0].special == "otsikko"

    def test_chapter_round_trip(self):
        """Chapter ref round-trips correctly."""
        original, round_trip = _shadow_compare("kumotaan 3 luku")
        assert round_trip == original == ["K L 3"]


# ===========================================================================
# Golden tests: part refs
# ===========================================================================


class TestPartRefGolden:
    """Golden tests for part reference lifting."""

    def test_part_whole(self):
        """'muutetaan 1 osa' lifts to SurfaceTargetRef(kind=PART, label='1')."""
        clause = parse_to_surface("muutetaan 1 osa")
        vg = clause.verb_groups[0]
        node = vg.nodes[0]
        assert isinstance(node, SurfaceTargetRef)
        assert node.kind == TargetKind.PART
        assert node.label == "1"

    def test_part_roman(self):
        """'muutetaan III ja V osa' lifts two part refs."""
        clause = parse_to_surface("muutetaan III ja V osa")
        vg = clause.verb_groups[0]
        assert len(vg.nodes) == 2
        labels = [n.label for n in vg.nodes if isinstance(n, SurfaceTargetRef)]
        assert labels == ["III", "V"]

    def test_part_otsikko(self):
        """'muutetaan VI osan otsikko' lifts with otsikko sub_ref."""
        clause = parse_to_surface("muutetaan VI osan otsikko")
        vg = clause.verb_groups[0]
        node = vg.nodes[0]
        assert isinstance(node, SurfaceTargetRef)
        assert node.kind == TargetKind.PART
        assert node.label == "VI"
        assert len(node.sub_refs) == 1
        assert node.sub_refs[0].special == "otsikko"

    def test_part_context_section(self):
        """'muutetaan II osan 1 luvun 3 §' lifts section with part context."""
        clause = parse_to_surface("muutetaan II osan 1 luvun 3 §")
        vg = clause.verb_groups[0]
        node = vg.nodes[0]
        assert isinstance(node, SurfaceTargetRef)
        assert node.kind == TargetKind.SECTION
        assert node.label == "3"
        assert node.chapter == "1"
        assert node.part == "II"


# ===========================================================================
# Golden tests: nimike refs
# ===========================================================================


class TestNimikeRefGolden:
    """Golden tests for nimike (title) reference lifting."""

    def test_nimike(self):
        """'muutetaan nimike ja 1 §' lifts nimike + section."""
        clause = parse_to_surface("muutetaan nimike ja 1 §")
        vg = clause.verb_groups[0]
        assert len(vg.nodes) == 2
        nimike_node = vg.nodes[0]
        assert isinstance(nimike_node, SurfaceTargetRef)
        assert nimike_node.kind == TargetKind.NIMIKE
        assert nimike_node.label == ""

        section_node = vg.nodes[1]
        assert isinstance(section_node, SurfaceTargetRef)
        assert section_node.kind == TargetKind.SECTION
        assert section_node.label == "1"


# ===========================================================================
# Golden tests: appendix refs
# ===========================================================================


class TestAppendixRefGolden:
    """Golden tests for appendix (liite) reference lifting."""

    def test_appendix(self):
        """'muutetaan 1 § ja liite' lifts section + appendix."""
        clause = parse_to_surface("muutetaan 1 § ja liite")
        vg = clause.verb_groups[0]
        assert len(vg.nodes) == 2
        appendix_node = vg.nodes[1]
        assert isinstance(appendix_node, SurfaceTargetRef)
        assert appendix_node.kind == TargetKind.APPENDIX

    def test_appendix_round_trip(self):
        """Appendix round-trips correctly."""
        original, round_trip = _shadow_compare("muutetaan 1 § ja liite")
        assert round_trip == original


# ===========================================================================
# Golden tests: verb group structure
# ===========================================================================


class TestVerbGroupStructure:
    """Tests that verb group boundaries are preserved correctly."""

    def test_single_verb_group(self):
        """Single verb group stays as one group."""
        clause = parse_to_surface("muutetaan 5 ja 6 §")
        assert len(clause.verb_groups) == 1
        assert clause.verb_groups[0].verb == VerbKind.MUUTTAA

    def test_two_verb_groups(self):
        """'kumotaan 3 § sekä muutetaan 5 §' produces two verb groups."""
        clause = parse_to_surface("kumotaan 3 § sekä muutetaan 5 §")
        assert len(clause.verb_groups) == 2
        assert clause.verb_groups[0].verb == VerbKind.KUMOTA
        assert clause.verb_groups[1].verb == VerbKind.MUUTTAA

    def test_three_verb_groups(self):
        """Three verbs produce three verb groups."""
        text = "kumotaan 3 §, muutetaan 5 § sekä lisätään 7 §:ään uusi 2 momentti"
        clause = parse_to_surface(text)
        assert len(clause.verb_groups) == 3
        verbs = [vg.verb for vg in clause.verb_groups]
        assert verbs == [VerbKind.KUMOTA, VerbKind.MUUTTAA, VerbKind.LISATA]

    def test_multi_verb_round_trip(self):
        """Multi-verb clause round-trips correctly."""
        text = "kumotaan 3 §, muutetaan 5 § sekä lisätään 7 §:ään uusi 2 momentti"
        original, round_trip = _shadow_compare(text)
        assert round_trip == original


# ===========================================================================
# Golden tests: insertion ops
# ===========================================================================


class TestInsertionGolden:
    """Tests that insertion ops are correctly lifted and round-tripped."""

    def test_insertion_momentti(self):
        """'lisätään 8 §:ään uusi 3 momentti' round-trips."""
        original, round_trip = _shadow_compare("lisätään 8 §:ään uusi 3 momentti")
        assert round_trip == original == ["L P 8 3"]

    def test_insertion_law_level(self):
        """'lisätään lakiin uusi 5 a §' round-trips."""
        original, round_trip = _shadow_compare("lisätään lakiin uusi 5 a §")
        assert round_trip == original == ["L P 5a"]

    def test_insertion_chapter(self):
        """'lisätään lakiin uusi 3 luku' round-trips."""
        original, round_trip = _shadow_compare("lisätään lakiin uusi 3 luku")
        assert round_trip == original == ["L L 3"]


# ===========================================================================
# Golden tests: renumber ops
# ===========================================================================


class TestRenumberGolden:
    """Tests that renumber ops are correctly lifted."""

    def test_renumber_single(self):
        """Renumber single section round-trips."""
        original, round_trip = _shadow_compare("muutetaan 1 §:n numero 3:ksi")
        assert round_trip == original

    def test_renumber_notes_preserved(self):
        """Renumber notes are carried through the round-trip."""
        clause = parse_to_surface("muutetaan 1 §:n numero 3:ksi")
        vg = clause.verb_groups[0]
        node = vg.nodes[0]
        assert isinstance(node, SurfaceTargetRef)
        assert "renumber_clause" in node.notes


# ===========================================================================
# Batch 2: descendant ref golden tests (surface-node fidelity)
# ===========================================================================


class TestDescendantSingleMomentti:
    """Batch 2: single momentti refs surface correctly."""

    def test_single_momentti_one_node(self):
        """'muutetaan 7 §:n 2 momentti' -> 1 node, 1 sub_ref."""
        clause = parse_to_surface("muutetaan 7 §:n 2 momentti")
        vg = clause.verb_groups[0]
        assert len(vg.nodes) == 1
        node = vg.nodes[0]
        assert isinstance(node, SurfaceTargetRef)
        assert node.label == "7"
        assert node.sub_refs == (SurfaceSubRef(momentti=2, item="", facet=None),)

    def test_single_momentti_with_chapter(self):
        """'muutetaan 3 luvun 12 §:n 2 momentti' -> 1 node with chapter context."""
        clause = parse_to_surface("muutetaan 3 luvun 12 §:n 2 momentti")
        vg = clause.verb_groups[0]
        assert len(vg.nodes) == 1
        node = vg.nodes[0]
        assert isinstance(node, SurfaceTargetRef)
        assert node.label == "12"
        assert node.chapter == "3"
        assert node.sub_refs == (SurfaceSubRef(momentti=2, item="", facet=None),)

    def test_single_momentti_round_trip(self):
        """Single momentti round-trips correctly."""
        original, round_trip = _shadow_compare("muutetaan 7 §:n 2 momentti")
        assert round_trip == original == ["M P 7 2"]


class TestDescendantSingleKohta:
    """Batch 2: single kohta refs surface correctly."""

    def test_single_kohta_one_node(self):
        """'muutetaan 5 §:n 1 momentin 3 kohta' -> 1 node with nested sub_ref."""
        clause = parse_to_surface("muutetaan 5 §:n 1 momentin 3 kohta")
        vg = clause.verb_groups[0]
        assert len(vg.nodes) == 1
        node = vg.nodes[0]
        assert isinstance(node, SurfaceTargetRef)
        assert node.label == "5"
        assert len(node.sub_refs) == 1
        sr = node.sub_refs[0]
        assert sr.momentti == 1
        assert sr.item == "3"
        assert sr.special == ""

    def test_single_kohta_round_trip(self):
        """Single kohta round-trips correctly."""
        original, round_trip = _shadow_compare("muutetaan 5 §:n 1 momentin 3 kohta")
        assert round_trip == original == ["M P 5 1 3"]


class TestDescendantHeadingIntro:
    """Batch 2: heading and intro special refs surface correctly."""

    def test_otsikko_surface(self):
        """'muutetaan 7 §:n otsikko' -> SurfaceSubRef(special='otsikko')."""
        clause = parse_to_surface("muutetaan 7 §:n otsikko")
        vg = clause.verb_groups[0]
        assert len(vg.nodes) == 1
        node = vg.nodes[0]
        assert isinstance(node, SurfaceTargetRef)
        assert node.label == "7"
        assert node.sub_refs == (SurfaceSubRef(momentti=0, item="", facet=FacetKind.HEADING, special="otsikko"),)

    def test_johd_surface(self):
        """'muutetaan 7 §:n johdantokappale' -> SurfaceSubRef(special='johd')."""
        clause = parse_to_surface("muutetaan 7 §:n johdantokappale")
        vg = clause.verb_groups[0]
        assert len(vg.nodes) == 1
        node = vg.nodes[0]
        assert isinstance(node, SurfaceTargetRef)
        assert node.label == "7"
        assert node.sub_refs == (SurfaceSubRef(momentti=0, item="", facet=FacetKind.INTRO, special="johd"),)

    def test_johd_with_momentti_surface(self):
        """'muutetaan 7 §:n 1 momentin johdantokappale' -> johd + momentti."""
        clause = parse_to_surface("muutetaan 7 §:n 1 momentin johdantokappale")
        vg = clause.verb_groups[0]
        assert len(vg.nodes) == 1
        node = vg.nodes[0]
        assert isinstance(node, SurfaceTargetRef)
        assert node.label == "7"
        assert node.sub_refs == (SurfaceSubRef(momentti=1, item="", facet=FacetKind.INTRO, special="johd"),)

    def test_otsikko_round_trip(self):
        """Otsikko round-trips correctly."""
        original, round_trip = _shadow_compare("muutetaan 7 §:n otsikko")
        assert round_trip == original == ["M P 7 o"]

    def test_johd_round_trip(self):
        """Johdantokappale round-trips correctly."""
        original, round_trip = _shadow_compare("muutetaan 7 §:n johdantokappale")
        assert round_trip == original == ["M P 7 j"]

    def test_dual_momentti_johd_surface(self):
        """'muutetaan 20 §:n 2 ja 3 momentin johdantokappale' -> two sub_refs with INTRO facet."""
        clause = parse_to_surface("muutetaan 20 §:n 2 ja 3 momentin johdantokappale")
        vg = clause.verb_groups[0]
        assert len(vg.nodes) == 1
        node = vg.nodes[0]
        assert isinstance(node, (SurfaceTargetRef, SurfaceDescendantCoordination))
        arms = node.sub_refs if isinstance(node, SurfaceTargetRef) else node.arms
        momentti_vals = [sr.momentti for sr in arms]
        facets = [sr.facet for sr in arms]
        assert 2 in momentti_vals
        assert 3 in momentti_vals
        assert all(f == FacetKind.INTRO for f in facets)

    def test_dual_momentti_johd_parse_clause_two_ops(self):
        """'muutetaan 20 §:n 2 ja 3 momentin johdantokappale' -> two INTRO ops."""
        result = parse_clause("muutetaan 20 §:n 2 ja 3 momentin johdantokappale")
        codes = [op.code() for op in result.parsed_ops]
        assert "M P 20 2 j" in codes
        assert "M P 20 3 j" in codes
        assert len(codes) == 2

    def test_dual_momentti_plain_parse_clause_two_ops(self):
        """'muutetaan 5 §:n 1 ja 2 momentti' -> two whole-momentti ops."""
        result = parse_clause("muutetaan 5 §:n 1 ja 2 momentti")
        codes = [op.code() for op in result.parsed_ops]
        assert "M P 5 1" in codes
        assert "M P 5 2" in codes
        assert len(codes) == 2


class TestDescendantMultiMomettiKohta:
    """Recursive coordination: multiple momenti sharing a kohta qualifier."""

    def test_dual_momentti_shared_kohta(self):
        """'2 ja 3 momentin 1 kohta' -> two ops with different momenti, same item."""
        result = parse_clause("muutetaan 5 §:n 2 ja 3 momentin 1 kohta")
        codes = sorted([op.code() for op in result.parsed_ops])
        assert codes == ["M P 5 2 1", "M P 5 3 1"]

    def test_dual_momentti_shared_kohta_round_trip(self):
        """'2 ja 3 momentin 1 kohta' round-trips correctly."""
        original, round_trip = _shadow_compare("muutetaan 5 §:n 2 ja 3 momentin 1 kohta")
        assert sorted(round_trip) == sorted(original) == ["M P 5 2 1", "M P 5 3 1"]

    def test_triple_momentti_shared_johd(self):
        """'1 ja 2 ja 3 momentin johdantokappale' -> three INTRO ops."""
        result = parse_clause("muutetaan 5 §:n 1 ja 2 ja 3 momentin johdantokappale")
        codes = sorted([op.code() for op in result.parsed_ops])
        assert codes == ["M P 5 1 j", "M P 5 2 j", "M P 5 3 j"]

    def test_dual_momentti_shared_otsikko(self):
        """'2 ja 3 momentin otsikko' -> two HEADING ops."""
        result = parse_clause("muutetaan 5 §:n 2 ja 3 momentin otsikko")
        codes = sorted([op.code() for op in result.parsed_ops])
        assert codes == ["M P 5 2 o", "M P 5 3 o"]

    def test_mixed_depth_cross_momentti(self):
        """'2 momentin 1 ja 3 kohdan sekä 4 momentin 1 kohdan' -> 3 ops."""
        text = "muutetaan 70 §:n 2 momentin 1 ja 3 kohdan sekä 4 momentin 1 kohdan"
        result = parse_clause(text)
        codes = sorted([op.code() for op in result.parsed_ops])
        assert codes == ["M P 70 2 1", "M P 70 2 3", "M P 70 4 1"]


class TestDescendantHomogeneousKohtaList:
    """Batch 2: homogeneous kohta list consolidation."""

    def test_kohta_list_consolidates_to_one_node(self):
        """'muutetaan 70 §:n 2 momentin 1 ja 3 kohta' -> 1 coordination node."""
        clause = parse_to_surface("muutetaan 70 §:n 2 momentin 1 ja 3 kohta")
        vg = clause.verb_groups[0]
        assert len(vg.nodes) == 1
        node = vg.nodes[0]
        assert isinstance(node, SurfaceDescendantCoordination)
        assert node.base.label == "70"
        assert len(node.arms) == 2
        assert node.arms[0] == SurfaceSubRef(momentti=2, item="1", facet=None)
        assert node.arms[1] == SurfaceSubRef(momentti=2, item="3", facet=None)

    def test_kohta_list_round_trip(self):
        """Homogeneous kohta list round-trips correctly."""
        original, round_trip = _shadow_compare("muutetaan 70 §:n 2 momentin 1 ja 3 kohta")
        assert round_trip == original == ["M P 70 2 1", "M P 70 2 3"]

    def test_kohta_range_consolidates(self):
        """'muutetaan 70 §:n 2 momentin 1-3 kohta' -> 1 coordination node."""
        clause = parse_to_surface("muutetaan 70 §:n 2 momentin 1\u20133 kohta")
        vg = clause.verb_groups[0]
        assert len(vg.nodes) == 1
        node = vg.nodes[0]
        assert isinstance(node, SurfaceDescendantCoordination)
        assert node.base.label == "70"
        assert len(node.arms) == 3
        items = [sr.item for sr in node.arms]
        assert items == ["1", "2", "3"]

    def test_kohta_range_round_trip(self):
        """Kohta range round-trips correctly."""
        original, round_trip = _shadow_compare("muutetaan 70 §:n 2 momentin 1\u20133 kohta")
        assert round_trip == original == ["M P 70 2 1", "M P 70 2 2", "M P 70 2 3"]


class TestDescendantMomenttiList:
    """Batch 2: mixed momentti list consolidation."""

    def test_momentti_list_consolidates(self):
        """'muutetaan 7 §:n 1 ja 2 momentti' -> 1 coordination node."""
        clause = parse_to_surface("muutetaan 7 §:n 1 ja 2 momentti")
        vg = clause.verb_groups[0]
        assert len(vg.nodes) == 1
        node = vg.nodes[0]
        assert isinstance(node, SurfaceDescendantCoordination)
        assert node.base.label == "7"
        assert len(node.arms) == 2
        assert node.arms[0] == SurfaceSubRef(momentti=1, item="", facet=None)
        assert node.arms[1] == SurfaceSubRef(momentti=2, item="", facet=None)

    def test_momentti_list_round_trip(self):
        """Momentti list round-trips correctly."""
        original, round_trip = _shadow_compare("muutetaan 7 §:n 1 ja 2 momentti")
        assert round_trip == original == ["M P 7 1", "M P 7 2"]

    def test_momentti_range_consolidates(self):
        """'muutetaan 16 c §:n 1-3 momentti' -> 1 coordination node."""
        clause = parse_to_surface("muutetaan 16 c §:n 1\u20143 momentti")
        vg = clause.verb_groups[0]
        assert len(vg.nodes) == 1
        node = vg.nodes[0]
        assert isinstance(node, SurfaceDescendantCoordination)
        assert node.base.label == "16c"
        assert len(node.arms) == 3
        momenti = [sr.momentti for sr in node.arms]
        assert momenti == [1, 2, 3]


class TestDescendantAlternatingDepth:
    """Batch 2: alternating-depth descendant coordination."""

    def test_cross_momentti_kohta_consolidates(self):
        """'muutetaan 70 §:n 2 momentin 1 ja 3 kohdan sekä 4 momentin 1 kohdan'
        -> 1 coordination node with 3 arms spanning different momentti."""
        text = "muutetaan 70 §:n 2 momentin 1 ja 3 kohdan sekä 4 momentin 1 kohdan"
        clause = parse_to_surface(text)
        vg = clause.verb_groups[0]
        assert len(vg.nodes) == 1
        node = vg.nodes[0]
        assert isinstance(node, SurfaceDescendantCoordination)
        assert node.base.label == "70"
        assert len(node.arms) == 3
        assert node.arms[0] == SurfaceSubRef(momentti=2, item="1", facet=None)
        assert node.arms[1] == SurfaceSubRef(momentti=2, item="3", facet=None)
        assert node.arms[2] == SurfaceSubRef(momentti=4, item="1", facet=None)

    def test_cross_momentti_kohta_round_trip(self):
        """Cross-momentti kohta round-trips correctly."""
        text = "muutetaan 70 §:n 2 momentin 1 ja 3 kohdan sekä 4 momentin 1 kohdan"
        original, round_trip = _shadow_compare(text)
        assert round_trip == original == ["M P 70 2 1", "M P 70 2 3", "M P 70 4 1"]


class TestDescendantComplexMixed:
    """Batch 2: complex mixed descendant patterns with consolidation."""

    def test_johd_kohta_momentti_consolidated(self):
        """Section 48 with johd + kohta + kohta + momentti -> 1 consolidated node."""
        text = (
            "muutetaan 48 §:n 1 momentin johdantokappale, "
            "2 ja 4 kohta sekä 5 momentti, "
            "49 a §:n 2 momentti, 50 §, 51 §:n 3 momentti ja 53 §"
        )
        clause = parse_to_surface(text)
        vg = clause.verb_groups[0]

        # Section 48 should be consolidated into 1 coordination node with 4 arms
        node48 = vg.nodes[0]
        assert isinstance(node48, SurfaceDescendantCoordination)
        assert node48.base.label == "48"
        assert len(node48.arms) == 4
        assert node48.arms[0] == SurfaceSubRef(momentti=1, item="", facet=FacetKind.INTRO, special="johd")
        assert node48.arms[1] == SurfaceSubRef(momentti=1, item="2", facet=None)
        assert node48.arms[2] == SurfaceSubRef(momentti=1, item="4", facet=None)
        assert node48.arms[3] == SurfaceSubRef(momentti=5, item="", facet=None)

        # Remaining sections are separate nodes
        assert isinstance(vg.nodes[1], SurfaceTargetRef)
        assert vg.nodes[1].label == "49a"
        assert isinstance(vg.nodes[2], SurfaceTargetRef)
        assert vg.nodes[2].label == "50"
        assert vg.nodes[2].sub_refs == ()  # whole target
        assert isinstance(vg.nodes[3], SurfaceTargetRef)
        assert vg.nodes[3].label == "51"
        assert isinstance(vg.nodes[4], SurfaceTargetRef)
        assert vg.nodes[4].label == "53"
        assert vg.nodes[4].sub_refs == ()  # whole target

    def test_johd_kohta_momentti_round_trip(self):
        """Complex mixed descendant round-trips correctly."""
        text = (
            "muutetaan 48 §:n 1 momentin johdantokappale, "
            "2 ja 4 kohta sekä 5 momentti, "
            "49 a §:n 2 momentti, 50 §, 51 §:n 3 momentti ja 53 §"
        )
        original, round_trip = _shadow_compare(text)
        assert (
            round_trip
            == original
            == [
                "M P 48 1 j",
                "M P 48 1 2",
                "M P 48 1 4",
                "M P 48 5",
                "M P 49a 2",
                "M P 50",
                "M P 51 3",
                "M P 53",
            ]
        )

    def test_otsikko_plus_momentti_consolidated(self):
        """'muutetaan 14 §:n otsikon ... 14 §:n 1 ja 3 momentti' consolidation."""
        text = "muutetaan 14 §:n otsikon ruotsinkielinen sanamuoto, 14 §:n 1 ja 3 momentti, 14 c § sekä 19 ja 20 §"
        clause = parse_to_surface(text)
        vg = clause.verb_groups[0]

        # The parser keeps the heading amendment on its own node and emits the
        # momentti coordination as a separate descendant node.
        node14 = vg.nodes[0]
        assert isinstance(node14, SurfaceTargetRef)
        assert node14.label == "14"
        assert node14.sub_refs == (SurfaceSubRef(facet=FacetKind.HEADING, special="otsikko"),)

        node14_moments = vg.nodes[1]
        assert isinstance(node14_moments, SurfaceDescendantCoordination)
        assert node14_moments.base.label == "14"
        assert len(node14_moments.arms) == 2
        assert node14_moments.arms[0].momentti == 1
        assert node14_moments.arms[1].momentti == 3

    def test_otsikko_plus_momentti_round_trip(self):
        """Mixed otsikko + momentti round-trips correctly."""
        text = "muutetaan 14 §:n otsikon ruotsinkielinen sanamuoto, 14 §:n 1 ja 3 momentti, 14 c § sekä 19 ja 20 §"
        original, round_trip = _shadow_compare(text)
        assert (
            round_trip
            == original
            == [
                "M P 14 o",
                "M P 14 1",
                "M P 14 3",
                "M P 14c",
                "M P 19",
                "M P 20",
            ]
        )

    def test_kieliasu_qualifier_keeps_later_section_list_alive(self):
        """A bare `kieliasu` qualifier must not terminate the later section list."""
        text = "muutetaan 2 §:n suomenkielinen kieliasu, 8, 9 ja 10 §"
        original, round_trip = _shadow_compare(text)
        assert (
            round_trip
            == original
            == [
                "M P 2",
                "M P 8",
                "M P 9",
                "M P 10",
            ]
        )


class TestDescendantNoFalseConsolidation:
    """Batch 2: verify consolidation does NOT merge where it should not."""

    def test_different_sections_stay_separate(self):
        """Ops on different sections remain separate nodes."""
        clause = parse_to_surface("muutetaan 7 §:n 1 momentti ja 8 §:n 2 momentti")
        vg = clause.verb_groups[0]
        assert len(vg.nodes) == 2
        n0, n1 = vg.nodes[0], vg.nodes[1]
        assert isinstance(n0, SurfaceTargetRef)
        assert isinstance(n1, SurfaceTargetRef)
        assert n0.label == "7"
        assert n1.label == "8"

    def test_whole_target_not_merged_with_sub_ref(self):
        """A whole-section op is not merged with a sub-ref op for the same section."""
        # "muutetaan 5 § ja 5 §:n 2 momentti" -- if the parser ever produces
        # [M P 5, M P 5 2], the whole-target op should stay standalone.
        ops = [
            ParsedOp(verb="M", kind="P", chapter="", number="5", momentti=0, item="", facet=None, raw=""),
            ParsedOp(verb="M", kind="P", chapter="", number="5", momentti=2, item="", facet=None, raw=""),
        ]
        for op in ops:
            op.raw = op.code()
        clause = lift_parsed_ops_to_surface_clause(ops)
        vg = clause.verb_groups[0]
        assert len(vg.nodes) == 2
        n0, n1 = vg.nodes[0], vg.nodes[1]
        assert isinstance(n0, SurfaceTargetRef)
        assert isinstance(n1, SurfaceTargetRef)
        assert n0.sub_refs == ()  # whole target standalone
        assert len(n1.sub_refs) == 1

    def test_different_chapter_context_stays_separate(self):
        """Ops with different chapter contexts for same label remain separate."""
        ops = [
            ParsedOp(verb="M", kind="P", chapter="3", number="5", momentti=1, item="", facet=None, raw=""),
            ParsedOp(verb="M", kind="P", chapter="4", number="5", momentti=2, item="", facet=None, raw=""),
        ]
        for op in ops:
            op.raw = op.code()
        clause = lift_parsed_ops_to_surface_clause(ops)
        vg = clause.verb_groups[0]
        assert len(vg.nodes) == 2

    def test_non_consecutive_same_section_stays_separate(self):
        """Same section appearing non-consecutively is NOT consolidated."""
        ops = [
            ParsedOp(verb="M", kind="P", chapter="", number="7", momentti=1, item="", facet=None, raw=""),
            ParsedOp(verb="M", kind="P", chapter="", number="8", momentti=0, item="", facet=None, raw=""),
            ParsedOp(verb="M", kind="P", chapter="", number="7", momentti=3, item="", facet=None, raw=""),
        ]
        for op in ops:
            op.raw = op.code()
        clause = lift_parsed_ops_to_surface_clause(ops)
        vg = clause.verb_groups[0]
        assert len(vg.nodes) == 3  # no cross-gap consolidation

    def test_renumber_backref_same_section_stays_separate(self):
        """Renumber section + backref whole section are not consolidated.

        "muutetaan 11 §:n numero 13:ksi ja mainittu pykälä" produces
        [M P 11 (renumber), M P 11 (whole)] — the whole-target should not
        consolidate with the renumber node.
        """
        text = "muutetaan 11 §:n numero 13:ksi ja mainittu pykälä"
        clause = parse_to_surface(text)
        vg = clause.verb_groups[0]
        # Both are whole-target ops for section 11, but the first has
        # renumber_clause notes and renumber_dest -- different anchor key
        assert len(vg.nodes) == 2


# ===========================================================================
# Lift-and-lower round-trip on raw ParsedOps
# ===========================================================================


class TestLiftLowerRoundTrip:
    """Direct lift -> lower round-trip on ParsedOp lists."""

    def test_single_op(self):
        """Single op round-trips through lift -> lower."""
        op = ParsedOp(verb="M", kind="P", chapter="", number="7", momentti=0, item="", facet=None, raw="")
        op.raw = op.code()

        clause = lift_parsed_ops_to_surface_clause([op])
        result = lower_surface_clause_to_parsed_ops(clause)

        assert _codes(result) == _codes([op])

    def test_op_with_chapter(self):
        """Op with chapter context round-trips."""
        op = ParsedOp(verb="M", kind="P", chapter="3", number="12", momentti=2, item="", facet=None, raw="")
        op.raw = op.code()

        clause = lift_parsed_ops_to_surface_clause([op])
        result = lower_surface_clause_to_parsed_ops(clause)

        assert _codes(result) == ["M P L:3 12 2"]
        assert result[0].chapter == "3"
        assert result[0].momentti == 2

    def test_op_with_part(self):
        """Op with part context round-trips."""
        op = ParsedOp(verb="M", kind="P", chapter="1", number="3", momentti=0, item="", facet=None, raw="", part="II")
        op.raw = op.code()

        clause = lift_parsed_ops_to_surface_clause([op])
        result = lower_surface_clause_to_parsed_ops(clause)

        assert _codes(result) == ["M P O:II L:1 3"]

    def test_multi_verb_group(self):
        """Multiple verb groups round-trip."""
        ops = [
            ParsedOp(verb="K", kind="P", chapter="", number="3", momentti=0, item="", facet=None, raw=""),
            ParsedOp(verb="M", kind="P", chapter="", number="5", momentti=0, item="", facet=None, raw=""),
        ]
        for op in ops:
            op.raw = op.code()

        clause = lift_parsed_ops_to_surface_clause(ops)
        result = lower_surface_clause_to_parsed_ops(clause)

        assert _codes(result) == ["K P 3", "M P 5"]

    def test_chapter_op(self):
        """Chapter kind op round-trips."""
        op = ParsedOp(verb="K", kind="L", chapter="", number="3", momentti=0, item="", facet=None, raw="")
        op.raw = op.code()

        clause = lift_parsed_ops_to_surface_clause([op])
        result = lower_surface_clause_to_parsed_ops(clause)

        assert _codes(result) == ["K L 3"]

    def test_appendix_op(self):
        """Appendix kind op round-trips."""
        op = ParsedOp(verb="M", kind="A", chapter="", number="", momentti=0, item="", facet=None, raw="")
        op.raw = op.code()

        clause = lift_parsed_ops_to_surface_clause([op])
        result = lower_surface_clause_to_parsed_ops(clause)

        assert _codes(result) == ["M A "]

    def test_nimike_op(self):
        """Nimike kind op round-trips."""
        op = ParsedOp(verb="M", kind="N", chapter="", number="", momentti=0, item="", facet=None, raw="")
        op.raw = op.code()

        clause = lift_parsed_ops_to_surface_clause([op])
        result = lower_surface_clause_to_parsed_ops(clause)

        assert _codes(result) == ["M N "]

    def test_part_op(self):
        """Part kind op round-trips."""
        op = ParsedOp(verb="M", kind="O", chapter="", number="III", momentti=0, item="", facet=None, raw="")
        op.raw = op.code()

        clause = lift_parsed_ops_to_surface_clause([op])
        result = lower_surface_clause_to_parsed_ops(clause)

        assert _codes(result) == ["M O III"]

    def test_otsikko_sub_ref(self):
        """Otsikko sub-ref round-trips."""
        op = ParsedOp(
            verb="M", kind="P", chapter="", number="6", momentti=0, item="", facet=FacetKind.HEADING, raw=""
        )
        op.raw = op.code()

        clause = lift_parsed_ops_to_surface_clause([op])
        result = lower_surface_clause_to_parsed_ops(clause)

        assert _codes(result) == ["M P 6 o"]

    def test_johd_sub_ref(self):
        """Johd sub-ref round-trips."""
        op = ParsedOp(
            verb="M", kind="P", chapter="", number="15", momentti=1, item="", facet=FacetKind.INTRO, raw=""
        )
        op.raw = op.code()

        clause = lift_parsed_ops_to_surface_clause([op])
        result = lower_surface_clause_to_parsed_ops(clause)

        assert _codes(result) == ["M P 15 1 j"]

    def test_kohta_sub_ref(self):
        """Kohta sub-ref round-trips."""
        op = ParsedOp(verb="M", kind="P", chapter="", number="5", momentti=1, item="3", facet=None, raw="")
        op.raw = op.code()

        clause = lift_parsed_ops_to_surface_clause([op])
        result = lower_surface_clause_to_parsed_ops(clause)

        assert _codes(result) == ["M P 5 1 3"]

    def test_empty_produces_empty(self):
        """Empty op list round-trips to empty."""
        clause = lift_parsed_ops_to_surface_clause([])
        result = lower_surface_clause_to_parsed_ops(clause)
        assert result == []

    def test_renumber_dest_preserved(self):
        """renumber_dest field survives round-trip."""
        op = ParsedOp(
            verb="M",
            kind="P",
            chapter="",
            number="1",
            momentti=0,
            item="",
            facet=None,
            raw="",
            notes=("renumber_clause",),
            renumber_dest="3",
        )
        op.raw = op.code()

        clause = lift_parsed_ops_to_surface_clause([op])
        result = lower_surface_clause_to_parsed_ops(clause)

        assert _codes(result) == ["M P 1"]
        assert result[0].renumber_dest == "3"
        assert "renumber_clause" in result[0].notes

    # --- Batch 2: consolidated sub_ref round-trips ---

    def test_consolidated_momentti_pair(self):
        """Two same-section momentti ops consolidate and round-trip."""
        ops = [
            ParsedOp(verb="M", kind="P", chapter="", number="7", momentti=1, item="", facet=None, raw=""),
            ParsedOp(verb="M", kind="P", chapter="", number="7", momentti=3, item="", facet=None, raw=""),
        ]
        for op in ops:
            op.raw = op.code()

        clause = lift_parsed_ops_to_surface_clause(ops)
        # Consolidated into 1 coordination node
        assert len(clause.verb_groups[0].nodes) == 1
        node = clause.verb_groups[0].nodes[0]
        assert isinstance(node, SurfaceDescendantCoordination)
        assert len(node.arms) == 2

        result = lower_surface_clause_to_parsed_ops(clause)
        assert _codes(result) == ["M P 7 1", "M P 7 3"]

    def test_consolidated_kohta_pair(self):
        """Two same-section kohta ops consolidate and round-trip."""
        ops = [
            ParsedOp(verb="M", kind="P", chapter="", number="70", momentti=2, item="1", facet=None, raw=""),
            ParsedOp(verb="M", kind="P", chapter="", number="70", momentti=2, item="3", facet=None, raw=""),
        ]
        for op in ops:
            op.raw = op.code()

        clause = lift_parsed_ops_to_surface_clause(ops)
        assert len(clause.verb_groups[0].nodes) == 1
        node = clause.verb_groups[0].nodes[0]
        assert isinstance(node, SurfaceDescendantCoordination)
        assert len(node.arms) == 2
        assert node.arms[0].item == "1"
        assert node.arms[1].item == "3"

        result = lower_surface_clause_to_parsed_ops(clause)
        assert _codes(result) == ["M P 70 2 1", "M P 70 2 3"]

    def test_consolidated_mixed_depth_round_trip(self):
        """Cross-momentti kohta ops consolidate and round-trip."""
        ops = [
            ParsedOp(verb="M", kind="P", chapter="", number="70", momentti=2, item="1", facet=None, raw=""),
            ParsedOp(verb="M", kind="P", chapter="", number="70", momentti=2, item="3", facet=None, raw=""),
            ParsedOp(verb="M", kind="P", chapter="", number="70", momentti=4, item="1", facet=None, raw=""),
        ]
        for op in ops:
            op.raw = op.code()

        clause = lift_parsed_ops_to_surface_clause(ops)
        assert len(clause.verb_groups[0].nodes) == 1
        node = clause.verb_groups[0].nodes[0]
        assert isinstance(node, SurfaceDescendantCoordination)
        assert len(node.arms) == 3

        result = lower_surface_clause_to_parsed_ops(clause)
        assert _codes(result) == ["M P 70 2 1", "M P 70 2 3", "M P 70 4 1"]

    def test_consolidated_johd_plus_kohta_round_trip(self):
        """Johdantokappale + kohta on same section consolidate and round-trip."""
        ops = [
            ParsedOp(
                verb="M", kind="P", chapter="", number="48", momentti=1, item="", facet=FacetKind.INTRO, raw=""
            ),
            ParsedOp(verb="M", kind="P", chapter="", number="48", momentti=1, item="2", facet=None, raw=""),
            ParsedOp(verb="M", kind="P", chapter="", number="48", momentti=5, item="", facet=None, raw=""),
        ]
        for op in ops:
            op.raw = op.code()

        clause = lift_parsed_ops_to_surface_clause(ops)
        assert len(clause.verb_groups[0].nodes) == 1
        node = clause.verb_groups[0].nodes[0]
        assert isinstance(node, SurfaceDescendantCoordination)
        assert len(node.arms) == 3
        assert node.arms[0].special == "johd"
        assert node.arms[1].item == "2"
        assert node.arms[2].momentti == 5

        result = lower_surface_clause_to_parsed_ops(clause)
        assert _codes(result) == ["M P 48 1 j", "M P 48 1 2", "M P 48 5"]
