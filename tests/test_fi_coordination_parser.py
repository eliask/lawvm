"""Tests for cross-depth coordination patterns in the johtolause parser.

Verifies that _parse_descendant_coordination() and the separator loop in
_sub_ref() correctly handle conjunction across different structural depths
(momentti, kohta, facet).

Patterns are drawn from Lainkirjoittajan opas and real Finnish statute
amendment preambles.
"""

from __future__ import annotations

from lawvm.core.semantic_types import FacetKind
from lawvm.finland.johtolause.api import parse_clause


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
# Alakohta: preserved in the target item label
# ---------------------------------------------------------------------------


class TestAlakohta:
    """Alakohta tokens are preserved instead of silently broadening to kohta."""

    def test_alakohta_preserved_in_target_item_label(self):
        """1 momentin 2 kohdan a alakohta keeps the alakohta as its own level.

        The kohta (``2``) and alakohta (``a``) are distinct hierarchy levels in
        the canonical model, emitted as separate code tokens rather than a
        collapsed ``2a`` compound.
        """
        codes = _ops("muutetaan 70 §:n 1 momentin 2 kohdan a alakohta")
        assert codes == ["M P 70 1 2 a"]


# ---------------------------------------------------------------------------
# Production parser behaviour preserved from the retired surface-migration
# shadow-test: these pin parse_clause op output directly (independent of any
# lift/round-trip adapter).
# ---------------------------------------------------------------------------


class TestDualMomenttiSharedQualifier:
    """parse_clause emits one op per momentti when a qualifier is shared."""

    def test_dual_momentti_johd_two_ops(self):
        """'2 ja 3 momentin johdantokappale' -> two INTRO ops."""
        codes = _ops("muutetaan 20 §:n 2 ja 3 momentin johdantokappale")
        assert "M P 20 2 j" in codes
        assert "M P 20 3 j" in codes
        assert len(codes) == 2

    def test_dual_momentti_plain_two_ops(self):
        """'1 ja 2 momentti' -> two whole-momentti ops."""
        codes = _ops("muutetaan 5 §:n 1 ja 2 momentti")
        assert "M P 5 1" in codes
        assert "M P 5 2" in codes
        assert len(codes) == 2

    def test_dual_momentti_shared_kohta(self):
        """'2 ja 3 momentin 1 kohta' -> two ops, different momentti, same item."""
        codes = sorted(_ops("muutetaan 5 §:n 2 ja 3 momentin 1 kohta"))
        assert codes == ["M P 5 2 1", "M P 5 3 1"]

    def test_triple_momentti_shared_johd(self):
        """'1 ja 2 ja 3 momentin johdantokappale' -> three INTRO ops."""
        codes = sorted(_ops("muutetaan 5 §:n 1 ja 2 ja 3 momentin johdantokappale"))
        assert codes == ["M P 5 1 j", "M P 5 2 j", "M P 5 3 j"]

    def test_dual_momentti_shared_otsikko(self):
        """'2 ja 3 momentin otsikko' -> two HEADING ops."""
        codes = sorted(_ops("muutetaan 5 §:n 2 ja 3 momentin otsikko"))
        assert codes == ["M P 5 2 o", "M P 5 3 o"]

    def test_mixed_depth_cross_momentti(self):
        """'2 momentin 1 ja 3 kohdan sekä 4 momentin 1 kohdan' -> 3 ops."""
        codes = sorted(
            _ops("muutetaan 70 §:n 2 momentin 1 ja 3 kohdan sekä 4 momentin 1 kohdan")
        )
        assert codes == ["M P 70 2 1", "M P 70 2 3", "M P 70 4 1"]
