"""Regression: chapter-heading drafted as ``N luvun nimike``.

A chapter's own heading can be drafted as ``N luvun nimike`` rather than the more
common ``N luvun otsikko``; both name the chapter title (HEADING facet). The old
parser only consumed ``otsikko`` (and ``johdantokappale``) after a chapter scope,
so on a ``N luvun nimike`` it stopped at the unconsumed ``nimike`` and silently
dropped the entire rest of the chapter-scoped list (the heading itself plus every
following section / range that inherited the chapter scope).

The grammar parser now recognizes ``N luvun nimike`` as a chapter HEADING facet,
so the chapter scope carries forward into the rest of the list exactly as it does
for ``N luvun otsikko``. These are the corpus exemplars the recovery covers; each
asserts the verbatim johtolause text (no oracle/replay dependency) to its expected
op codes. The ``nimike`` and ``otsikko`` spellings must produce identical ops.
"""
from __future__ import annotations

import pytest

from lawvm.finland.johtolause.api import parse_clause


# (name, johtolause text, expected op codes)
_NIMIKE_HEADING_CASES = [
    (
        # 1969/323 — two chapter groups, each ``N luvun nimike ja <section>``.
        "1969_323_two_chapter_nimike_groups",
        "muutetaan oikeudenkäymiskaaren 1 luvun nimike ja 10 §, sellaisena kuin "
        "se on 27 päivänä huhtikuuta 1868 annetussa asetuksessa, sekä 23 luvun "
        "nimike ja 1 §, sellaisena kuin se on 19 päivänä joulukuuta 1921 "
        "annetussa laissa ( 274/21 ), näin kuuluviksi:",
        ["M L 1 o", "M P L:1 10"],
    ),
    (
        # 2004/1410 — ``2 luvun nimike`` then a full chapter-2-scoped section list.
        "2004_1410_chapter_nimike_then_section_list",
        "kumotaan öljyvahinkojen ja aluskemikaalivahinkojen torjunnasta 28 "
        "päivänä kesäkuuta 1993 annetun valtioneuvoston asetuksen ( 636/1993 ) "
        "14 §, muutetaan 2 luvun nimike, 2 §:n 1 ja 2 momentti, 3―5 §, 6 §:n 1 "
        "momentti, 7 §:n 1 momentin 1―3 kohta ja 12 §, lisätään 7 §:n 1 "
        "momenttiin uusi 8 a kohta seuraavasti:",
        [
            "K P 14",
            "M L 2 o",
            "M P L:2 2 1",
            "M P L:2 2 2",
            "M P L:2 3",
            "M P L:2 4",
            "M P L:2 5",
            "M P L:2 6 1",
            "M P L:2 7 1 1",
            "M P L:2 7 1 2",
            "M P L:2 7 1 3",
            "M P L:2 12",
            "L P 7 1 8a",
        ],
    ),
    (
        # 1937/249 — bare ``6 luvun nimike`` whose HEADING facet the old parser
        # dropped (CHAPTER node with no facet).
        "1937_249_chapter_nimike_heading_facet",
        "Eduskunnan päätöksen mukaisesti, joka on tehty valtiopäiväjärjestyksen "
        "67 §:ssa määrätyllä tavalla, muutetaan valtiopäiväjärjestyksen 6 luvun "
        "nimike ja valtiopäiväjärjestykseen lisätään uusi 83 a § seuraavasti:",
        ["M L 6 o", "L P 83a"],
    ),
]


@pytest.mark.parametrize(
    "text,expected",
    [(t, e) for _name, t, e in _NIMIKE_HEADING_CASES],
    ids=[name for name, _t, _e in _NIMIKE_HEADING_CASES],
)
def test_chapter_nimike_heading_recovered(text: str, expected: list[str]) -> None:
    result = parse_clause(text)
    actual = [op.code() for op in result.parsed_ops]
    assert actual == expected, (
        f"\nInput:    {text[:120]}\nExpected: {expected}\nActual:   {actual}"
    )


def test_chapter_nimike_equals_otsikko() -> None:
    """``N luvun nimike`` and ``N luvun otsikko`` produce identical ops — the
    ``nimike`` spelling is just an alternate drafting of the chapter heading."""
    nimike = parse_clause("muutetaan 2 luvun nimike ja 10 §")
    otsikko = parse_clause("muutetaan 2 luvun otsikko ja 10 §")
    assert [op.code() for op in nimike.parsed_ops] == [
        op.code() for op in otsikko.parsed_ops
    ] == ["M L 2 o", "M P L:2 10"]
