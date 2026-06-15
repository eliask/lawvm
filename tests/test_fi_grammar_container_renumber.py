"""Recognizer-level tests for the container renumber / coordinated-heading arms
and the chapter-backref resumption.

These four shapes are the recognizer-half of the curated cases the new parser
declined before this slice:

  * ``fi.chapter_renumber`` — ``… N luvun numero M:ksi`` (CHAPTER_RENUMBER form)
  * ``fi.part_renumber`` — ``… N osan numero M:ksi`` (PART_RENUMBER form)
  * ``fi.coordinated_part_chapter_heading_ref`` — ``N osan ja M luvun otsikko``
    (COORDINATED_HEADING form)
  * chapter-backref resumption — ``mainitun luvun <section_ref>`` (resumes a
    previously-named chapter and names sections under it)

The objective gate is byte-identity to the OLD parser (``surface_parse.parse``):
each test recognizes the shape at its cursor, emits the nodes, and compares them
to the nodes the old parser produced for the same clause (kind, label, scope,
witness rule_id + span, notes, renumber_dest, sub_refs).  The recognizers are
NOT wired into ``grammar/parser.py`` yet (that is the driver's job), so these
tests drive the recognizers directly — no end-to-end pipeline needed.
"""

from __future__ import annotations

import pytest

from lawvm.finland.johtolause import surface_parse
from lawvm.finland.johtolause.grammar.backrefs import (
    ParsedChapterBackref,
    emit_chapter_backref_nodes,
    recognize_chapter_backref,
)
from lawvm.finland.johtolause.grammar.combinators import Cursor
from lawvm.finland.johtolause.grammar.containers import (
    ContainerForm,
    emit_containers_nodes,
    recognize_containers,
)
from lawvm.finland.johtolause.grammar.sections import _Scan
from lawvm.finland.johtolause.lexer import tokenize
from lawvm.finland.johtolause.scan import apply_annotations_with_jolloin_pairs
from lawvm.finland.johtolause.surface_model import SurfaceNode, SurfaceTargetRef, TargetKind


def _rule_id(node: SurfaceTargetRef) -> str:
    """The witness rule_id, asserting the witness is present (type-narrowing)."""
    assert node.witness is not None
    return node.witness.rule_id


def _filtered(text: str):
    raw = tokenize(text)
    tokens, _ = apply_annotations_with_jolloin_pairs(raw)
    return tokens


def _scan_after_verb(text: str) -> _Scan:
    """A scan positioned just after the first amendment VERB."""
    tokens = _filtered(text)
    i = next(i for i, t in enumerate(tokens) if t.cat == "VERB") + 1
    return _Scan(Cursor(tokens, i))


def _old_nodes(text: str) -> list[SurfaceNode]:
    """All target nodes the OLD parser emits for ``text`` (flattened)."""
    raw = tokenize(text)
    tokens, jolloin = apply_annotations_with_jolloin_pairs(raw)
    clause = surface_parse.parse(tokens, jolloin_renumber_pairs=jolloin or None)
    return [n for vg in clause.verb_groups for n in vg.nodes]


# ---------------------------------------------------------------------------
# Chapter / part renumber arms.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "form"),
    [
        ("Muutetaan 3 luvun numero 5:ksi", ContainerForm.CHAPTER_RENUMBER),
        ("Muutetaan luku 3 numero 5:ksi", ContainerForm.CHAPTER_RENUMBER),
        ("Muutetaan 1-3 luvun numero 26-28:ksi", ContainerForm.CHAPTER_RENUMBER),
        ("Muutetaan II osan numero III:ksi", ContainerForm.PART_RENUMBER),
    ],
)
def test_renumber_recognizer_byte_identical_to_old(text: str, form: ContainerForm) -> None:
    scan = _scan_after_verb(text)
    parsed = recognize_containers(scan)
    assert parsed is not None
    assert parsed.form is form
    nodes = emit_containers_nodes(parsed)
    assert nodes == _old_nodes(text)


def test_chapter_renumber_destination_shape() -> None:
    scan = _scan_after_verb("Muutetaan 3 luvun numero 5:ksi")
    parsed = recognize_containers(scan)
    assert parsed is not None
    assert parsed.form is ContainerForm.CHAPTER_RENUMBER
    (node,) = emit_containers_nodes(parsed)
    assert isinstance(node, SurfaceTargetRef)
    assert node.kind is TargetKind.CHAPTER
    assert node.label == "3"
    assert node.renumber_dest == "5"
    assert node.notes == ("renumber_clause", "renumber_destination=5")
    assert _rule_id(node) == "fi.chapter_renumber"


def test_part_renumber_destination_shape() -> None:
    scan = _scan_after_verb("Muutetaan II osan numero III:ksi")
    parsed = recognize_containers(scan)
    assert parsed is not None
    assert parsed.form is ContainerForm.PART_RENUMBER
    (node,) = emit_containers_nodes(parsed)
    assert isinstance(node, SurfaceTargetRef)
    assert node.kind is TargetKind.PART
    assert node.label == "II"
    assert node.renumber_dest == "III"
    assert node.notes == ("renumber_clause", "renumber_destination=III")
    assert _rule_id(node) == "fi.part_renumber"


def test_chapter_renumber_range_distributes_destinations() -> None:
    """``1-3 luvun numero 26-28:ksi`` pairs each source chapter to its dest."""
    scan = _scan_after_verb("Muutetaan 1-3 luvun numero 26-28:ksi")
    parsed = recognize_containers(scan)
    assert parsed is not None
    nodes = emit_containers_nodes(parsed)
    pairs = [
        (n.label, n.renumber_dest)
        for n in nodes
        if isinstance(n, SurfaceTargetRef)
    ]
    assert pairs == [("1", "26"), ("2", "27"), ("3", "28")]


# ---------------------------------------------------------------------------
# Coordinated part+chapter heading shape.
# ---------------------------------------------------------------------------


def test_coordinated_heading_recognizer_byte_identical_to_old() -> None:
    text = "Muutetaan II osan ja 5 luvun otsikko"
    scan = _scan_after_verb(text)
    parsed = recognize_containers(scan)
    assert parsed is not None
    assert parsed.form is ContainerForm.COORDINATED_HEADING
    nodes = emit_containers_nodes(parsed)
    assert nodes == _old_nodes(text)


def test_coordinated_heading_emits_part_then_chapter() -> None:
    from lawvm.core.semantic_types import FacetKind

    scan = _scan_after_verb("Muutetaan II osan ja 5 luvun otsikko")
    parsed = recognize_containers(scan)
    assert parsed is not None
    part_node, chapter_node = emit_containers_nodes(parsed)
    assert isinstance(part_node, SurfaceTargetRef)
    assert part_node.kind is TargetKind.PART
    assert part_node.label == "II"
    assert part_node.sub_refs[0].facet is FacetKind.HEADING
    assert _rule_id(part_node) == "fi.coordinated_part_chapter_heading_ref"
    assert isinstance(chapter_node, SurfaceTargetRef)
    assert chapter_node.kind is TargetKind.CHAPTER
    assert chapter_node.label == "5"
    assert chapter_node.part == "II"  # part scope carried onto the chapter target
    assert chapter_node.sub_refs[0].facet is FacetKind.HEADING
    assert _rule_id(chapter_node) == "fi.chapter_ref"


# ---------------------------------------------------------------------------
# Chapter-backref resumption: "mainitun luvun <section_ref>".
# ---------------------------------------------------------------------------


def _chapter_backref_clause() -> str:
    # Two verb groups: the first names "3 luvun 5 §" (so chapter 3 is the
    # last-named chapter), the second resumes it with "mainitun luvun 6 §".
    return "Muutetaan 3 luvun 5 § sekä lisätään mainitun luvun 6 §"


def test_chapter_backref_recognizer_consumes_prefix_and_section() -> None:
    tokens = _filtered(_chapter_backref_clause())
    br_pos = next(i for i, t in enumerate(tokens) if t.cat == "BACKREF")
    scan = _Scan(Cursor(tokens, br_pos))
    parsed = recognize_chapter_backref(scan)
    assert parsed is not None
    assert isinstance(parsed, ParsedChapterBackref)
    # Prefix span covers BACKREF + LUKU only; the section ref keeps its own span.
    assert parsed.prefix_span.start == br_pos
    assert parsed.prefix_span.end == br_pos + 2
    assert parsed.inner_section.nums == (("6", ""),)


def test_chapter_backref_emits_section_scoped_to_resumed_chapter() -> None:
    text = _chapter_backref_clause()
    tokens = _filtered(text)
    br_pos = next(i for i, t in enumerate(tokens) if t.cat == "BACKREF")
    scan = _Scan(Cursor(tokens, br_pos))
    parsed = recognize_chapter_backref(scan)
    assert parsed is not None
    nodes = emit_chapter_backref_nodes(parsed, chapter="3")
    # Byte-identical to the section node the OLD parser emits for the resumption.
    old_section_nodes = [
        n
        for n in _old_nodes(text)
        if isinstance(n, SurfaceTargetRef) and n.kind is TargetKind.SECTION and n.label == "6"
    ]
    assert nodes == old_section_nodes
    (node,) = nodes
    assert isinstance(node, SurfaceTargetRef)
    assert node.chapter == "3"  # resumed chapter scope applied
    assert _rule_id(node) == "fi.section_ref"


def test_chapter_backref_declines_when_not_followed_by_luku() -> None:
    """``mainitun pykälän …`` is the SECTION backref, not the chapter resumption.

    The chapter-backref recognizer must back out fully (cursor rewound) so the
    driver routes ``BACKREF PYKALA`` to the section-backref family instead.
    """
    tokens = _filtered("Kumotaan 3 § ja mainitun pykälän 2 momentti")
    br_pos = next(i for i, t in enumerate(tokens) if t.cat == "BACKREF")
    assert tokens[br_pos + 1].cat != "LUKU"
    scan = _Scan(Cursor(tokens, br_pos))
    parsed = recognize_chapter_backref(scan)
    assert parsed is None
    assert scan.pos == br_pos  # nothing consumed
