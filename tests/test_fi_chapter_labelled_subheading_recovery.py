"""Regression: letter-labelled intra-chapter subheadings interleaved in a list.

A chapter-scoped amendment list can interleave LETTER-LABELLED intra-chapter
subheadings (``C väliotsikko``, ``D alaluvun otsikko``, ``alalukujen B ja C
väliotsikko``) with section ranges:

    6 luvun C väliotsikko, 14–18 §, D väliotsikko, 19–27 §, 33 §:n 1 momentti
    ja 36 §

These subheadings name a sub-section heading WITHIN the chapter (a cross-heading),
not the chapter's own title, but they still scope the following section list to
the chapter. The old parser had no production for the letter-labelled shape and
stopped at the unconsumed letter, collapsing the whole chapter-scoped list to the
bare chapter — silently dropping the subheading plus every following section /
range that inherited the chapter scope.

The grammar parser now recognizes the labelled-subheading shape (leading after the
``N luvun`` scope, and mid-list inheriting the running chapter) as a chapter
HEADING facet, label-discriminated by the subheading letter, so the chapter scope
carries forward through the rest of the list. Each exemplar asserts the verbatim
johtolause text (no oracle/replay dependency) to its expected op codes; all are
new-is-better deltas the legacy parser dropped.

The disambiguation against a real section letter is structural: a subheading label
is an UPPERCASE single letter that closes on an OTSIKKO heading noun, whereas a
section suffix is a LOWERCASE letter bound to a following ``§`` (``4 a §``). The
negative test pins that a ``N a §`` section is never read as a subheading.
"""
from __future__ import annotations

import pytest

from lawvm.finland.johtolause.api import parse_clause


# (name, johtolause text, expected op codes)
_LABELLED_SUBHEADING_CASES = [
    (
        # 2013/798 — leading ``6 luvun C väliotsikko`` then a mid-list ``D
        # väliotsikko``, both scoping the section ranges to chapter 6. The old
        # parser emitted only ``M L 6`` and dropped the rest.
        "2013_798_C_then_midlist_D_valiotsikko",
        "muutetaan kirkkojärjestyksen ( 1055/1993 ) 6 luvun C väliotsikko, "
        "14–18 §, D väliotsikko, 19–27 §, 33 §:n 1 momentti ja 36 §, sellaisina "
        "kuin ne ovat kirkolliskokouksen 5.11.2009 tekemässä päätöksessä, "
        "seuraavasti:",
        [
            "M L 6 o",
            "M P L:6 14",
            "M P L:6 15",
            "M P L:6 16",
            "M P L:6 17",
            "M P L:6 18",
            "M L 6 o",
            "M P L:6 19",
            "M P L:6 20",
            "M P L:6 21",
            "M P L:6 22",
            "M P L:6 23",
            "M P L:6 24",
            "M P L:6 25",
            "M P L:6 26",
            "M P L:6 27",
            "M P L:6 33 1",
            "M P L:6 36",
        ],
    ),
    (
        # 2014/415 — ``6 luvun C alaluvun otsikko`` (letter BEFORE the
        # ``alaluvun`` marker) leading a chapter-6 section list, plus a kumotaan
        # ``6 luvun D alaluvun otsikko`` group. Multi-verb (kumotaan / muutetaan /
        # lisätään); each chapter-scoped subheading carries the chapter scope.
        "2014_415_alaluvun_otsikko_multi_verb",
        "kumotaan kirkkojärjestyksen ( 1055/1993 ) 6 luvun D alaluvun otsikko ja "
        "21–27 § sekä 7 luvun 2 §, muutetaan 5 luvun 10 §, 6 luvun C alaluvun "
        "otsikko, 14–20 §, 33 §:n 1 momentti ja 36 § sekä 19 luvun 2 §, lisätään "
        "5 lukuun uusi 11 § sekä 18 lukuun uusi 1 b–1 d § seuraavasti:",
        [
            "K L 6 o",
            "K P L:6 21",
            "K P L:6 22",
            "K P L:6 23",
            "K P L:6 24",
            "K P L:6 25",
            "K P L:6 26",
            "K P L:6 27",
            "K P L:7 2",
            "M P L:5 10",
            "M L 6 o",
            "M P L:6 14",
            "M P L:6 15",
            "M P L:6 16",
            "M P L:6 17",
            "M P L:6 18",
            "M P L:6 19",
            "M P L:6 20",
            "M P L:6 33 1",
            "M P L:6 36",
            "M P L:19 2",
            "L P L:5 11",
            "L P L:18 1b",
            "L P L:18 1c",
            "L P L:18 1d",
        ],
    ),
    (
        # 2003/1278 — the ``alaluvun D väliotsikko`` (marker BEFORE the letter) and
        # the plural shared-heading ``alalukujen B ja C väliotsikko`` (two letters
        # sharing one OTSIKKO). Both scope to their preceding ``N luvun`` chapter.
        "2003_1278_alaluvun_D_and_plural_B_ja_C",
        "kumotaan 8 päivänä marraskuuta 1991 hyväksytyn kirkon vaalijärjestyksen "
        "( 1056/1993 ) 2 luvun 19 §:n 1 momentti sekä 3 luvun alaluvun D "
        "väliotsikko ja 10―12 §, muutetaan 1 luvun 1 §, 2 luvun 19 §:n 2 "
        "momentti, 21 §:n 1 momentti ja 22 §, 3 luvun alalukujen B ja C "
        "väliotsikko ja 8―9 § sekä 4 luvun 7 §,",
        [
            "K P L:2 19 1",
            "K L 3 o",
            "K P L:3 10",
            "K P L:3 11",
            "K P L:3 12",
            "M P L:1 1",
            "M P L:2 19 2",
            "M P L:2 21 1",
            "M P L:2 22",
            "M L 3 o",
            "M P L:3 8",
            "M P L:3 9",
            "M P L:4 7",
        ],
    ),
    (
        # 2013/799 — a mid-list ``C väliotsikko`` inside the chapter-2 muutetaan
        # list (``…2 kohta, C väliotsikko, 23 §, …``) inheriting the running
        # chapter-2 scope; the old parser stopped at ``C`` and dropped 23–29 §.
        "2013_799_midlist_C_valiotsikko_inherits_chapter",
        "kumotaan kirkon vaalijärjestyksen ( 1056/1993 ) 2 luvun 25 §, sellaisena "
        "kuin se on osaksi kirkolliskokouksen päätöksessä 239/2006, ja 28 §:n 2 "
        "ja 3 momentti, sekä muutetaan 1 luvun 1 §, 2 luvun otsikko, 10 §:n 3 "
        "momentti, 13 §:n 1 momentti, 15 §:n 2 momentin johdantokappale ja 2 "
        "kohta, C väliotsikko, 23 §, 24 §:n 1 momentti, 26 §, 27 §, 28 §:n 1 "
        "momentti ja 29 §:n 1 momentti,",
        [
            "K P L:2 25",
            "K P L:2 28 2",
            "K P L:2 28 3",
            "M P L:1 1",
            "M L 2 o",
            "M P L:2 10 3",
            "M P L:2 13 1",
            "M P L:2 15 2 j",
            "M P L:2 15 2 2",
            "M L 2 o",
            "M P L:2 23",
            "M P L:2 24 1",
            "M P L:2 26",
            "M P L:2 27",
            "M P L:2 28 1",
            "M P L:2 29 1",
        ],
    ),
]


@pytest.mark.parametrize(
    "text,expected",
    [(t, e) for _name, t, e in _LABELLED_SUBHEADING_CASES],
    ids=[name for name, _t, _e in _LABELLED_SUBHEADING_CASES],
)
def test_labelled_subheading_recovered(text: str, expected: list[str]) -> None:
    result = parse_clause(text)
    actual = [op.code() for op in result.parsed_ops]
    assert actual == expected, (
        f"\nInput:    {text[:120]}\nExpected: {expected}\nActual:   {actual}"
    )


def test_subheading_letter_not_confused_with_section_letter() -> None:
    """A lowercase section-letter ``N a §`` must NEVER be read as a subheading.

    ``6 luvun 4 a §`` is a chapter-scoped section ``4 a`` — the ``a`` is a section
    suffix bound to ``§``, not an uppercase subheading label closing on an
    OTSIKKO. The labelled-subheading recognizer must not fire here; the clause
    lowers to a single chapter-6-scoped section op, identical to the spelled-out
    form without the ``a`` suffix shape being misclassified.
    """
    section = parse_clause("muutetaan 6 luvun 4 a §")
    assert [op.code() for op in section.parsed_ops] == ["M P L:6 4a"]

    # And a chapter-scoped section whose number is a bare uppercase letter that is
    # NOT followed by an OTSIKKO (``6 luvun C §``) stays a section, not a heading.
    letter_section = parse_clause("muutetaan 6 luvun C §")
    codes = [op.code() for op in letter_section.parsed_ops]
    assert all("o" not in c.split() for c in codes), (
        f"A ``C §`` section must not produce a heading op: {codes!r}"
    )


def test_chapter_subheading_carries_scope_like_chapter_heading() -> None:
    """A leading labelled subheading scopes the following sections to its chapter.

    ``6 luvun C väliotsikko, 14 §`` must scope ``14 §`` to chapter 6, exactly as
    the chapter's own heading ``6 luvun otsikko, 14 §`` does — the subheading is a
    chapter-scope carrier, just label-discriminated.
    """
    sub = parse_clause("muutetaan 6 luvun C väliotsikko, 14 §")
    assert [op.code() for op in sub.parsed_ops] == ["M L 6 o", "M P L:6 14"]
