"""Tests for cross-depth coordination patterns in the johtolause parser.

Verifies that _parse_descendant_coordination() and the separator loop in
_sub_ref() correctly handle conjunction across different structural depths
(momentti, kohta, facet).

Patterns are drawn from Lainkirjoittajan opas and real Finnish statute
amendment preambles.
"""

from __future__ import annotations

from lawvm.core.semantic_types import FacetKind
from lawvm.finland.johtolause.compat import parse_clause


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ops(text: str) -> list[str]:
    """Parse amendment text and return op codes."""
    result = parse_clause(text)
    return [op.code() for op in result.parsed_ops]


def _sub_refs(text: str):
    """Parse amendment text and return (momentti, item, facet) tuples for each op."""
    result = parse_clause(text)
    return [
        (op.momentti, op.item, op.facet)
        for op in result.parsed_ops
    ]


# ---------------------------------------------------------------------------
# Cross-depth coordination: different momentin contexts across conjunction
# ---------------------------------------------------------------------------


class TestCrossDepthCoordination:
    """Cross-depth patterns where conjunction separates different momentin contexts."""

    def test_cross_mom_kohta_basic(self):
        """2 momentin 1 kohdan ja 3 momentin 2 kohdan -> two kohta-level refs."""
        codes = _ops("muutetaan 70 §:n 2 momentin 1 kohdan ja 3 momentin 2 kohdan")
        assert codes == ["M P 70 2 1", "M P 70 3 2"]

    def test_cross_mom_kohta_nominative(self):
        """1 momentin 2 kohta ja 2 momentin 3 kohta -> two kohta-level refs."""
        codes = _ops("muutetaan 70 §:n 1 momentin 2 kohta ja 2 momentin 3 kohta")
        assert codes == ["M P 70 1 2", "M P 70 2 3"]

    def test_cross_mom_kohta_with_trailing_intro(self):
        """2 momentin 1 kohdan ja 3 momentin 2 kohdan johdantolause.

        Trailing johdantolause distributes to BOTH kohta-level arms.
        """
        refs = _sub_refs("muutetaan 70 §:n 2 momentin 1 kohdan ja 3 momentin 2 kohdan johdantolause")
        assert refs == [
            (2, "1", FacetKind.INTRO),
            (3, "2", FacetKind.INTRO),
        ]

    def test_cross_mom_kohta_with_trailing_intro_op_codes(self):
        """Op code form of trailing johdantolause distribution."""
        codes = _ops("muutetaan 70 §:n 2 momentin 1 kohdan ja 3 momentin 2 kohdan johdantolause")
        assert codes == ["M P 70 2 1 j", "M P 70 3 2 j"]

    def test_mixed_conjunction_multi_mom_kohta(self):
        """1 ja 2 momentin 3 kohta ja 3 momentin 1 ja 2 kohta.

        First group: mom 1+2 share kohta 3.
        Second group: mom 3 has kohta 1+2.
        """
        codes = _ops("muutetaan 70 §:n 1 ja 2 momentin 3 kohta ja 3 momentin 1 ja 2 kohta")
        assert codes == ["M P 70 1 3", "M P 70 2 3", "M P 70 3 1", "M P 70 3 2"]


# ---------------------------------------------------------------------------
# Mixed-depth coordination: different structural levels across conjunction
# ---------------------------------------------------------------------------


class TestMixedDepthCoordination:
    """Patterns mixing momentti-only and kohta-level sub-refs."""

    def test_momentti_plus_deeper(self):
        """1 momentti ja 2 momentin 3 kohta -> section-level + kohta-level."""
        codes = _ops("muutetaan 70 §:n 1 momentti ja 2 momentin 3 kohta")
        assert codes == ["M P 70 1", "M P 70 2 3"]

    def test_mixed_depth_comma_conj(self):
        """2 momentti, 3 momentin johdantokappale ja 4 momentin 1 kohta.

        Three different depths: whole momentti, intro, kohta.
        """
        codes = _ops("muutetaan 70 §:n 2 momentti, 3 momentin johdantokappale ja 4 momentin 1 kohta")
        assert codes == ["M P 70 2", "M P 70 3 j", "M P 70 4 1"]

    def test_no_false_facet_distribution_across_depths(self):
        """2 momentti ja 3 momentin johdantokappale.

        The INTRO facet must NOT distribute to the nominative momentti arm.
        """
        refs = _sub_refs("muutetaan 70 §:n 2 momentti ja 3 momentin johdantokappale")
        assert refs == [
            (2, "", None),       # whole momentti, no facet
            (3, "", FacetKind.INTRO),  # intro only for mom 3
        ]


# ---------------------------------------------------------------------------
# Range + conjunction
# ---------------------------------------------------------------------------


class TestRangeCoordination:
    """Range patterns combined with conjunction across depths."""

    def test_range_plus_conjunction(self):
        """1-3 momentti ja 4 momentin 1 kohta."""
        codes = _ops("muutetaan 70 §:n 1\u20133 momentti ja 4 momentin 1 kohta")
        assert codes == ["M P 70 1", "M P 70 2", "M P 70 3", "M P 70 4 1"]


# ---------------------------------------------------------------------------
# Letter kohta patterns
# ---------------------------------------------------------------------------


class TestLetterKohtaCoordination:
    """Letter-identified kohta items in coordination."""

    def test_letter_kohta_conj(self):
        """1 momentin a-kohta ja b-kohta."""
        codes = _ops("muutetaan 70 §:n 1 momentin a-kohta ja b-kohta")
        # b-kohta inherits momentti context from the separator loop
        assert codes == ["M P 70 1 a", "M P 70 1 b"]


# ---------------------------------------------------------------------------
# Single coordination group (within _parse_descendant_coordination)
# ---------------------------------------------------------------------------


class TestSingleGroupCoordination:
    """Patterns handled entirely within _parse_descendant_coordination."""

    def test_conj_momentti(self):
        """2 ja 3 momentti."""
        codes = _ops("muutetaan 70 §:n 2 ja 3 momentti")
        assert codes == ["M P 70 2", "M P 70 3"]

    def test_conj_momentti_with_shared_kohta(self):
        """2 ja 3 momentin 1 kohta."""
        codes = _ops("muutetaan 70 §:n 2 ja 3 momentin 1 kohta")
        assert codes == ["M P 70 2 1", "M P 70 3 1"]

    def test_conj_momentti_with_shared_intro(self):
        """2 ja 3 momentin johdantokappale."""
        refs = _sub_refs("muutetaan 70 §:n 2 ja 3 momentin johdantokappale")
        assert refs == [
            (2, "", FacetKind.INTRO),
            (3, "", FacetKind.INTRO),
        ]

    def test_conj_kohta_under_momentti(self):
        """1 momentin 2 ja 3 kohta."""
        codes = _ops("muutetaan 70 §:n 1 momentin 2 ja 3 kohta")
        assert codes == ["M P 70 1 2", "M P 70 1 3"]

    def test_bare_kohta(self):
        """1 kohta (no momentti prefix)."""
        codes = _ops("muutetaan 70 §:n 1 kohta")
        assert codes == ["M P 70 1 1"]

    def test_kohta_genitive_with_intro(self):
        """1 kohdan johdantolause (no momentti prefix)."""
        refs = _sub_refs("muutetaan 70 §:n 1 kohdan johdantolause")
        assert refs == [(1, "1", FacetKind.INTRO)]


# ---------------------------------------------------------------------------
# Alakohta: deliberately stripped by the scan phase
# ---------------------------------------------------------------------------


class TestAlakohta:
    """Alakohta tokens are removed by the scan phase (design decision).

    The scan annotation phase strips ALAKOHTA and LETTER+ALAKOHTA patterns
    as qualifiers before the parser sees them.  This is documented in
    scan.py line 572: "Alakohta refinements -> removed".

    The parser therefore treats "1 momentin 2 kohdan a alakohta" the same
    as "1 momentin 2 kohdan" (the 'a alakohta' is consumed by scan as a
    qualifier annotation and never reaches the parser).
    """

    def test_alakohta_stripped_to_kohta(self):
        """1 momentin 2 kohdan a alakohta -> same as 1 momentin 2 kohdan.

        The 'a alakohta' tokens are removed by scan annotations, leaving
        only '1 momentin 2 kohdan' for the parser.
        """
        codes = _ops("muutetaan 70 §:n 1 momentin 2 kohdan a alakohta")
        # Parser sees: 1 momentin 2 kohdan (a alakohta stripped)
        assert codes == ["M P 70 1 2"]
