"""Worked examples for the SIIRTAA (move / renumber) recognizer family.

Each move recognizer is a faithful port of one context-free helper in
``surface_parse.py`` (``_parse_cross_verb_move_tail``,
``_parse_relabel_from_context``, ``_leading_move_destination_part``,
``_inline_move_clause_tail_destination``). The objective ground truth is the OLD
helper's output run on the IDENTICAL filtered token stream: these tests assert
the new recognizer + emitter reproduce the old helper's node fields AND its final
cursor position (including its rewind-vs-advance behavior) byte-for-byte.

There is no standalone single-SIIRTAA-verb-group clause subset in the corpus
(every move clause is entangled with the jolloin renumber path, cross-verb-group
context, or sibling verb-group families), so the differential is asserted at the
recognizer-helper level rather than via a full-clause ``compare_surface_parsers``
— see ``.tmp/validate_moves.py`` for the corpus-level characterization.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Optional

import pytest

from lawvm.finland.johtolause import surface_parse as sp
from lawvm.finland.johtolause.grammar import moves
from lawvm.finland.johtolause.grammar.combinators import Cursor
from lawvm.finland.johtolause.grammar.sections import _Scan
from lawvm.finland.johtolause.lexer import tokenize
from lawvm.finland.johtolause.scan import apply_annotations_with_jolloin_pairs


def _tokens(text: str):
    raw = tokenize(text)
    toks, _ = apply_annotations_with_jolloin_pairs(raw)
    return toks


def _scan(text: str) -> _Scan:
    return _Scan(Cursor(_tokens(text)))


def _node_fields(node: object) -> Optional[dict]:
    if node is None:
        return None
    assert is_dataclass(node) and not isinstance(node, type)
    return asdict(node)


# ---------------------------------------------------------------------------
# Cross-verb move retarget: [muutettu] N § M lukuun.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "muutettu 85 b § 9 lukuun",
        "85 b § 9 lukuun",
        "muutettu 85 b §, 9 lukuun",
        "5 § 3 lukuun",
    ],
)
def test_cross_verb_move_matches_old(text: str) -> None:
    old_s = sp.Stream(_tokens(text))
    old = sp._parse_cross_verb_move_tail(old_s)
    assert old is not None

    scan = _scan(text)
    parsed = moves.recognize_cross_verb_move_tail(scan)
    assert parsed is not None
    new_nodes = moves.emit_cross_verb_move_nodes(parsed)
    assert len(new_nodes) == 1

    assert _node_fields(new_nodes[0]) == _node_fields(old)
    assert scan.pos == old_s.pos


# ---------------------------------------------------------------------------
# Direct relabel from context: [N luvun] M §:ksi.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "7 luvun 61 §:ksi",
        "61 §:ksi",
        "5 luvun 9 §:ksi",
    ],
)
def test_relabel_matches_old(text: str) -> None:
    old_s = sp.Stream(_tokens(text))
    old = sp._parse_relabel_from_context(old_s)
    assert old is not None

    scan = _scan(text)
    parsed = moves.recognize_relabel_from_context(scan)
    assert parsed is not None
    new_nodes = moves.emit_relabel_nodes(parsed)
    assert len(new_nodes) == 1

    assert _node_fields(new_nodes[0]) == _node_fields(old)
    assert scan.pos == old_s.pos


# ---------------------------------------------------------------------------
# Leading move destination part: N osaan [,].
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("I osaan, 5 §", "I"),
        ("I osaan 5", "I"),
        ("II osaan,", "II"),
    ],
)
def test_leading_move_part_matches_old(text: str, expected: str) -> None:
    old_s = sp.Stream(_tokens(text))
    old = sp._leading_move_destination_part(old_s)

    scan = _scan(text)
    parsed = moves.recognize_leading_move_destination_part(scan)

    assert old == expected
    assert parsed is not None
    assert parsed.destination_part == old
    assert scan.pos == old_s.pos


# ---------------------------------------------------------------------------
# Inline move tail: , jotka samalla siirretään N lukuun / N osaan.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        (", jotka samalla siirretään 5 lukuun", ("5", "")),
        ("jotka samalla siirretään 5 lukuun", ("5", "")),
        (", jotka samalla siirretään I osaan", ("", "I")),
    ],
)
def test_inline_move_tail_matches_old(text: str, expected: tuple[str, str]) -> None:
    old_s = sp.Stream(_tokens(text))
    old = sp._inline_move_clause_tail_destination(old_s, None, None)

    scan = _scan(text)
    parsed = moves.recognize_inline_move_tail(scan)

    assert old == expected
    assert parsed is not None
    assert (parsed.destination_chapter, parsed.destination_part) == old
    assert scan.pos == old_s.pos


# ---------------------------------------------------------------------------
# Negative tests — shapes the family correctly DECLINES (None, no advance).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "recognizer,text",
    [
        # cross-verb needs a LUKU:ILL destination, not a part.
        (moves.recognize_cross_verb_move_tail, "muutettu 85 b § 9 osaan"),
        # relabel needs the §:ksi translative, not a bare §.
        (moves.recognize_relabel_from_context, "61 §"),
        # inline tail requires the SIIRTAA verb, not MUUTTAA.
        (moves.recognize_inline_move_tail, ", jotka samalla muutetaan 5 lukuun"),
        # inline tail requires a LUKU/OSA:ILL destination, not a bare §.
        (moves.recognize_inline_move_tail, ", jotka samalla siirretään 5 §"),
    ],
)
def test_declines_and_rewinds(recognizer, text: str) -> None:
    scan = _scan(text)
    start = scan.pos
    parsed = recognizer(scan)
    assert parsed is None
    assert scan.pos == start  # recoverable failure rewound the cursor


def test_emit_moves_nodes_dispatch() -> None:
    # The standalone forms emit nodes; the carrier-only forms emit nothing.
    cross = moves.recognize_cross_verb_move_tail(_scan("muutettu 85 b § 9 lukuun"))
    assert cross is not None
    assert len(moves.emit_moves_nodes(cross)) == 1

    relabel = moves.recognize_relabel_from_context(_scan("61 §:ksi"))
    assert relabel is not None
    assert len(moves.emit_moves_nodes(relabel)) == 1

    inline = moves.recognize_inline_move_tail(_scan(", jotka samalla siirretään 5 lukuun"))
    assert inline is not None
    assert moves.emit_moves_nodes(inline) == []  # carrier-only: retags a batch
