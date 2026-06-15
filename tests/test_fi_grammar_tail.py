"""Byte-identity tests for the tail recognizer family.

The tail rules (``fi.lukuun_ottamatta_exception`` /
``fi.insertion_section_postfix_chapter``) are rare and ENTANGLED — each fires
inside a larger section / insert batch, with no standalone single-family clause.
So these tests validate at the recognizer-HELPER level (per the rewrite guide's
entangled-family note): drive the OLD ``surface_parse.parse`` over a real clause,
extract the node(s) carrying the family's witness, then drive OUR recognizer +
emitter at the same anchor position and assert the canonical node form is
byte-identical to the OLD parser's.

Where the OLD parser supplies an inherited chapter/part context from a preceding
target batch in the verb-group loop (driver-level state, NOT recognizer state),
the test threads the same context into our emitter — that carry-forward is the
driver's job, deferred to integration, exactly as for the other entangled
families.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Optional

import pytest

from lawvm.finland.johtolause import surface_parse
from lawvm.finland.johtolause.grammar.combinators import Cursor
from lawvm.finland.johtolause.grammar.diff import _jsonify, parse_text_with
from lawvm.finland.johtolause.grammar.sections import _Scan
from lawvm.finland.johtolause.grammar.tail import (
    emit_exception_nodes,
    emit_postfix_insert_nodes,
    recognize_exception,
    recognize_postfix_insert,
)
from lawvm.finland.johtolause.lexer import tokenize
from lawvm.finland.johtolause.scan import apply_annotations_with_jolloin_pairs
from lawvm.finland.johtolause.surface_model import SurfaceNode


def _canon(node: SurfaceNode) -> Any:
    return _jsonify(asdict(node))


def _tokens(text: str):
    raw = tokenize(text)
    tokens, _ = apply_annotations_with_jolloin_pairs(raw)
    return tokens


def _old_nodes(text: str, rule_id: str) -> list[SurfaceNode]:
    """OLD-parser top-level nodes carrying ``rule_id`` (the ground truth)."""
    model = parse_text_with(text, surface_parse.parse)
    out: list[SurfaceNode] = []
    for vg in model.verb_groups:
        for node in vg.nodes:
            w = getattr(node, "witness", None)
            if w is not None and w.rule_id == rule_id:
                out.append(node)
    return out


def _our_exception(
    text: str, anchor: int, chapter: str = "", part: str = ""
) -> Optional[list[SurfaceNode]]:
    scan = _Scan(Cursor(_tokens(text), anchor))
    parsed = recognize_exception(scan)
    if parsed is None:
        return None
    return emit_exception_nodes(parsed, chapter=chapter, part=part)


def _our_postfix(
    text: str, anchor: int, part: str = ""
) -> Optional[list[SurfaceNode]]:
    scan = _Scan(Cursor(_tokens(text), anchor))
    parsed = recognize_postfix_insert(scan)
    if parsed is None:
        return None
    return emit_postfix_insert_nodes(parsed, part=part)


# ---------------------------------------------------------------------------
# fi.lukuun_ottamatta_exception
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, inherited_chapter",
    [
        # ScopeBlock branch: the excepted section carries an explicit chapter
        # prefix ("7 luvun 73 §"), so it is wrapped in a SurfaceScopeBlock.
        ("muutetaan 4-7 luku, lukuun ottamatta kuitenkaan 7 luvun 73 §:ää", ""),
        # Bare SurfaceTargetRef branch: the preceding batch is itself sections,
        # so no chapter is inherited.
        ("muutetaan 4-7 §, lukuun ottamatta kuitenkaan 73 §:ää", ""),
        # SurfaceTargetRef branch WITH inherited chapter: the preceding "1-7 luku"
        # batch leaves chapter "7" as the verb-group context, which the OLD parser
        # threads into the excepted bare "5 §". The driver supplies that context;
        # the test threads the same "7" into our emitter.
        ("muutetaan 1-7 luku, lukuun ottamatta 5 §:ää", "7"),
    ],
)
def test_exception_byte_identical(text: str, inherited_chapter: str) -> None:
    olds = _old_nodes(text, "fi.lukuun_ottamatta_exception")
    assert olds, "fixture must trigger the exception witness in the OLD parser"
    anchor = olds[0].witness.source_span[0]
    ours = _our_exception(text, anchor, chapter=inherited_chapter)
    assert ours is not None, "recognizer declined an OLD-classified exception"
    assert [_canon(n) for n in ours] == [_canon(n) for n in olds]


def test_exception_descendant_coordination_branch() -> None:
    # SurfaceDescendantCoordination branch: the excepted section has >=2 sub-refs
    # ("3 §:n 1 ja 2 momenttia"), so the section family emits a descendant
    # coordination, which the exception path re-stamps on its base.
    text = "muutetaan 1-5 §, lukuun ottamatta 3 §:n 1 ja 2 momenttia"
    olds = _old_nodes(text, "fi.lukuun_ottamatta_exception")
    assert olds
    anchor = olds[0].witness.source_span[0]
    ours = _our_exception(text, anchor)
    assert ours is not None
    assert [_canon(n) for n in ours] == [_canon(n) for n in olds]


def test_exception_declines_non_exception() -> None:
    # A plain section ref with no "lukuun ottamatta" must be DECLINED (None),
    # never miscompiled into an exception.
    scan = _Scan(Cursor(_tokens("muutetaan 12 §"), 1))
    assert recognize_exception(scan) is None


# ---------------------------------------------------------------------------
# fi.insertion_section_postfix_chapter
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # Shape A (§ lukuun N), multi-arm, coordinated by comma + "ja".
        "lisätään uuteen lakiin uusi 35 a § lukuun 5, 104 a § lukuun 6 ja 133 b § lukuun 7",
        # Shape B (§ N lukuun), single arm.
        "lisätään lakiin uusi 35 c § 5 lukuun",
    ],
)
def test_postfix_insert_byte_identical(text: str) -> None:
    olds = _old_nodes(text, "fi.insertion_section_postfix_chapter")
    assert olds, "fixture must trigger the postfix-chapter witness in the OLD parser"
    anchor = olds[0].witness.source_span[0]
    ours = _our_postfix(text, anchor)
    assert ours is not None, "recognizer declined an OLD-classified postfix insert"
    assert [_canon(n) for n in ours] == [_canon(n) for n in olds]


def test_postfix_insert_declines_plain_insert() -> None:
    # A bare "uusi 5 a §" insert with NO postfix chapter must be DECLINED, so the
    # generic insert path keeps handling it (the old helper returns None here).
    text = "lisätään lakiin uusi 5 a §"
    tokens = _tokens(text)
    # Anchor at the section number ("5"), just like the postfix helper is tried.
    anchor = next(i for i, t in enumerate(tokens) if t.cat == "NUM")
    scan = _Scan(Cursor(tokens, anchor))
    assert recognize_postfix_insert(scan) is None
