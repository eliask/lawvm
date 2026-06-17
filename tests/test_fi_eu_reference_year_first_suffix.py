"""Recognizer-level gate for the un-parenthesized year-first form-suffix arm.

The shared ``eu_reference`` recognizer previously had no pattern for the
un-parenthesized year-first form-suffix shape ``YEAR/NUMBER/FORM`` — e.g.
``direktiivin 2004/36/EY``, ``direktiiviä 2003/42/EY``. The existing prep-lane
patterns only matched parenthesized cites (``(FORM) YEAR/NUMBER`` and
``(FORM) N:o NUMBER/YEAR``), so an inline body cite in this shape with no
adjacent ``(EU)`` marker and no CELEX was lost outright by the preparatory
recognizer.

The ``DIALECT_PREPARATORY`` path now tries this shape LAST (after the two paren
forms), so it recovers the bare body cite while never overriding a parenthesized
cite in the same text.

No-collision invariants proved here:
  - the new arm is PREPARATORY-only: the CROSS_REF dialect is unchanged (it has
    its own year-first-slash pass in the cross_refs lane);
  - a number-first ``NUMBER/YEAR/FORM`` cite is NOT mis-split into a spurious
    year-first match (left-guard + 4-digit middle group);
  - a parenthesized ``(FORM) YEAR/NUMBER`` / ``(FORM) N:o NUMBER/YEAR`` cite
    still wins when present in the same text.
"""
from __future__ import annotations

from lawvm.finland.references.eu_reference import (
    DIALECT_CROSS_REF,
    DIALECT_PREPARATORY,
    recognize_eu_acts,
)


class TestYearFirstSuffixRecovery:
    """The year-first form-suffix shape resolves under DIALECT_PREPARATORY."""

    def test_directive_year_first_suffix_resolves(self) -> None:
        """'direktiivin 2004/36/EY' -> year=2004, number=36, form=EY."""
        refs = recognize_eu_acts("direktiivin 2004/36/EY", dialect=DIALECT_PREPARATORY)
        assert len(refs) == 1
        assert refs[0].year == "2004"
        assert refs[0].number == "36"
        assert refs[0].form == "EY"
        assert refs[0].raw == "2004/36/EY"

    def test_partitive_head_year_first_suffix_resolves(self) -> None:
        """'direktiiviä 2003/42/EY' -> year=2003, number=42 (head case-agnostic)."""
        refs = recognize_eu_acts("direktiiviä 2003/42/EY", dialect=DIALECT_PREPARATORY)
        assert len(refs) == 1
        assert refs[0].year == "2003"
        assert refs[0].number == "42"
        assert refs[0].form == "EY"

    def test_eu_form_year_first_suffix_resolves(self) -> None:
        """The 'EU' form variant resolves identically ('direktiivi 2011/83/EU')."""
        refs = recognize_eu_acts("direktiivi 2011/83/EU", dialect=DIALECT_PREPARATORY)
        assert len(refs) == 1
        assert refs[0].year == "2011"
        assert refs[0].number == "83"
        assert refs[0].form == "EU"

    def test_three_digit_act_number_resolves(self) -> None:
        """A 3-digit act number is still unambiguously year-first ('2008/122/EY')."""
        refs = recognize_eu_acts("direktiivi 2008/122/EY", dialect=DIALECT_PREPARATORY)
        assert len(refs) == 1
        assert refs[0].year == "2008"
        assert refs[0].number == "122"


class TestYearFirstSuffixNoCollision:
    """The new arm does not change any pre-existing recognized form."""

    def test_cross_ref_dialect_unchanged(self) -> None:
        """The arm is PREPARATORY-only — CROSS_REF still ignores this shape.

        The cross_refs lane recovers the year-first slash via its own pass, so
        the shared CROSS_REF recognizer must stay byte-identical (returns no
        eu-act span for a bare 'YEAR/NUMBER/FORM' cite, exactly as before).
        """
        assert recognize_eu_acts(
            "direktiivin 2004/36/EY", dialect=DIALECT_CROSS_REF
        ) == []

    def test_number_first_not_mis_split(self) -> None:
        """A number-first 'NUMBER/YEAR/FORM' cite is NOT read as year-first.

        '1234/2004/EY' has a 4-digit YEAR in the middle (not a <=3-digit act
        number), and the left-guard blocks the '/2004/EY' tail, so the
        year-first arm produces no match.
        """
        assert recognize_eu_acts(
            "asetus 1234/2004/EY", dialect=DIALECT_PREPARATORY
        ) == []

    def test_paren_modern_form_still_wins(self) -> None:
        """A parenthesized modern '(FORM) YEAR/NUMBER' cite is chosen, unchanged."""
        refs = recognize_eu_acts(
            "asetuksessa (EU) 2016/679", dialect=DIALECT_PREPARATORY
        )
        assert len(refs) == 1
        assert refs[0].raw == "(EU) 2016/679"
        assert refs[0].year == "2016"
        assert refs[0].number == "679"

    def test_paren_nro_form_still_wins(self) -> None:
        """A parenthesized N:o '(FORM) N:o NUMBER/YEAR' cite is chosen, unchanged."""
        refs = recognize_eu_acts(
            "asetuksessa (EY) N:o 999/2001", dialect=DIALECT_PREPARATORY
        )
        assert len(refs) == 1
        assert refs[0].raw == "(EY) N:o 999/2001"
        assert refs[0].number == "999"
        assert refs[0].year == "2001"

    def test_paren_form_preferred_over_year_first_suffix(self) -> None:
        """When BOTH shapes are present, the paren cite wins (arm is last-resort)."""
        refs = recognize_eu_acts(
            "direktiivin 2004/36/EY ja asetuksessa (EU) 2016/679",
            dialect=DIALECT_PREPARATORY,
        )
        assert len(refs) == 1
        assert refs[0].raw == "(EU) 2016/679"

    def test_bare_number_first_tail_no_spurious_match(self) -> None:
        """A standalone number-first '1234/2004/EY' yields no year-first match."""
        assert recognize_eu_acts(
            "1234/2004/EY", dialect=DIALECT_PREPARATORY
        ) == []
