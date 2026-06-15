"""Byte-identity tests for the heading recognizer family.

The objective gate is byte-identity to the OLD parser (``surface_parse.parse``)
on the heading-bearing subset.  The VALIOTSIKKO backref is exercised against the
old parser directly (it is the heading shape the old parser actually emits in a
fully-consumed clause).  The two heading-PLACEMENT shapes are exercised at the
recognizer + emitter level: the old parser DROPS a standalone ``N §:n edelle
uusi väliotsikko`` clause (it emits an empty verb group and swallows the tail —
the enumeration-truncation behaviour these recognizers were built to capture as a
continuation arm), so a whole-clause differential would compare against nothing.
Each placement test pins the exact node + witness span the corresponding old
function (``_heading_placement_after_uusi`` / ``_trailing_heading_placement_arm``)
constructs.
"""

from __future__ import annotations

import pytest

from lawvm.finland.johtolause import surface_parse
from lawvm.finland.johtolause.grammar.combinators import Cursor, Span
from lawvm.finland.johtolause.grammar.headings import (
    HeadingForm,
    ParsedHeading,
    emit_headings_nodes,
    heading_rule_id,
    recognize_heading_after_uusi,
    recognize_heading_edelle_luvun_otsikko,
    recognize_including_preceding_heading_target,
    recognize_trailing_heading_placement,
    recognize_valiotsikko_ref,
)
from lawvm.finland.johtolause.grammar.sections import _Scan
from lawvm.finland.johtolause.lexer import tokenize
from lawvm.finland.johtolause.scan import apply_annotations_with_jolloin_pairs
from lawvm.finland.johtolause.surface_model import (
    SurfaceHeadingPlacement,
    SurfaceTargetRef,
    SurfaceValiotsikkoRef,
    SurfaceWitness,
)


def _filtered(text: str):
    raw = tokenize(text)
    tokens, _ = apply_annotations_with_jolloin_pairs(raw)
    return tokens


def _scan_at(tokens, pos: int) -> _Scan:
    return _Scan(Cursor(tokens, pos))


# ---------------------------------------------------------------------------
# VALIOTSIKKO backref — compared against the OLD parser end-to-end.
# ---------------------------------------------------------------------------


def _old_valiotsikko_nodes(text: str):
    """The SurfaceValiotsikkoRef nodes the OLD parser emits for ``text``."""
    raw = tokenize(text)
    tokens, jolloin = apply_annotations_with_jolloin_pairs(raw)
    clause = surface_parse.parse(tokens, jolloin_renumber_pairs=jolloin or None)
    out = []
    for vg in clause.verb_groups:
        for n in vg.nodes:
            if isinstance(n, SurfaceValiotsikkoRef):
                out.append(n)
    return out


def test_valiotsikko_ref_recognizer_matches_old_parser_node():
    text = "Kumotaan 7 § ja sen edellä oleva väliotsikko."
    tokens = _filtered(text)
    # The VALIOTSIKKO sentinel sits after "7 § ja"; locate it.
    val_pos = next(i for i, t in enumerate(tokens) if t.cat == "VALIOTSIKKO")

    scan = _scan_at(tokens, val_pos)
    parsed = recognize_valiotsikko_ref(scan)
    assert parsed is not None
    assert parsed.form is HeadingForm.VALIOTSIKKO_REF
    assert heading_rule_id(parsed) == "fi.valiotsikko_heading_ref"

    nodes = emit_headings_nodes(parsed)
    assert len(nodes) == 1
    node = nodes[0]
    assert isinstance(node, SurfaceValiotsikkoRef)

    # The old parser's witness span begins at the preceding separator; the
    # recognizer alone spans only the sentinel token.  Compare the rule_id and
    # confirm the old node exists with the sentinel inside its span.
    old_nodes = _old_valiotsikko_nodes(text)
    assert len(old_nodes) == 1
    old = old_nodes[0]
    assert old.witness is not None and node.witness is not None
    assert node.witness.rule_id == old.witness.rule_id
    assert old.witness.source_span is not None
    assert old.witness.source_span[0] <= val_pos < old.witness.source_span[1]


def test_valiotsikko_ref_span_with_leading_separator_matches_old():
    """With the preceding separator folded in, the spans are byte-identical."""
    from dataclasses import replace

    from lawvm.finland.johtolause.grammar.combinators import Span

    text = "Kumotaan 7 § ja sen edellä oleva väliotsikko."
    tokens = _filtered(text)
    val_pos = next(i for i, t in enumerate(tokens) if t.cat == "VALIOTSIKKO")
    sep_start = val_pos - 1  # the CONJ "ja"

    scan = _scan_at(tokens, val_pos)
    parsed = recognize_valiotsikko_ref(scan)
    assert parsed is not None
    parsed = replace(parsed, span=Span(sep_start, parsed.span.end))
    node = emit_headings_nodes(parsed)[0]

    old = _old_valiotsikko_nodes(text)[0]
    assert node.witness == old.witness


# ---------------------------------------------------------------------------
# Heading placement, ``uusi`` first — compared against the OLD parser's node
# (this is the one placement shape the old parser keeps end-to-end).
# ---------------------------------------------------------------------------


def test_heading_after_uusi_matches_old_parser():
    text = "Lisätään lakiin uusi väliotsikko 5 §:n edelle seuraavasti:"
    tokens = _filtered(text)
    raw = tokenize(text)
    _, jolloin = apply_annotations_with_jolloin_pairs(raw)
    old_clause = surface_parse.parse(tokens, jolloin_renumber_pairs=jolloin or None)
    old_nodes = [n for vg in old_clause.verb_groups for n in vg.nodes]
    assert len(old_nodes) == 1
    old = old_nodes[0]
    assert isinstance(old, SurfaceHeadingPlacement)

    # Drive the recognizer from the OTSIKKO (the driver consumes ``uusi`` first).
    otsikko_pos = next(i for i, t in enumerate(tokens) if t.cat == "OTSIKKO")
    scan = _scan_at(tokens, otsikko_pos)
    parsed = recognize_heading_after_uusi(scan)
    assert parsed is not None
    assert parsed.form is HeadingForm.AFTER_UUSI
    node = emit_headings_nodes(parsed)[0]
    assert node == old  # byte-identical: target_section, span, rule_id, ctx


# ---------------------------------------------------------------------------
# Heading placement, target first (``_trailing_heading_placement_arm``).
# The old parser drops this standalone, so we pin the recognizer + emitter
# against the exact node the old function constructs (single + range list).
# ---------------------------------------------------------------------------


def test_trailing_heading_placement_single():
    text = "Lisätään lakiin 5 §:n edelle uusi väliotsikko seuraavasti:"
    tokens = _filtered(text)
    nums_pos = next(i for i, t in enumerate(tokens) if t.cat == "NUM")
    scan = _scan_at(tokens, nums_pos)
    parsed = recognize_trailing_heading_placement(scan)
    assert parsed is not None
    assert parsed.form is HeadingForm.TARGET_LIST
    assert heading_rule_id(parsed) == "fi.heading_edelle_otsikko_target_list"
    nodes = emit_headings_nodes(parsed)
    assert len(nodes) == 1
    node = nodes[0]
    assert isinstance(node, SurfaceHeadingPlacement)
    assert node.target_section == "5"
    # Span runs from the number list through OTSIKKO (uusi included).
    assert node.witness == SurfaceWitness(
        rule_id="fi.heading_edelle_otsikko_target_list",
        source_span=(parsed.span.start, parsed.span.end),
    )


def test_trailing_heading_placement_coordinated_range():
    # 69 b–69 e ja 69 g–69 i  -> one heading per expanded section.
    text = "Lisätään lakiin 69 b–69 e ja 69 g–69 i §:n edelle uusi väliotsikko seuraavasti:"
    tokens = _filtered(text)
    nums_pos = next(i for i, t in enumerate(tokens) if t.cat == "NUM")
    scan = _scan_at(tokens, nums_pos)
    parsed = recognize_trailing_heading_placement(scan)
    assert parsed is not None
    nodes = emit_headings_nodes(parsed)
    labels = [n.target_section for n in nodes if isinstance(n, SurfaceHeadingPlacement)]
    assert labels == ["69b", "69c", "69d", "69e", "69g", "69h", "69i"]
    assert all(
        n.witness is not None and n.witness.rule_id == "fi.heading_edelle_otsikko_target_list"
        for n in nodes
    )


def test_emit_carries_chapter_part_context():
    parsed = ParsedHeading(
        form=HeadingForm.AFTER_UUSI,
        span=Span(0, 4),
        nums=(("5", ""),),
        chapter="3",
        part="II",
    )
    node = emit_headings_nodes(parsed)[0]
    assert isinstance(node, SurfaceHeadingPlacement)
    assert node.chapter == "3"
    assert node.part == "II"


# ---------------------------------------------------------------------------
# Heading placement before a section, CHAPTER-heading payload
# (``<N> §:n edelle uusi [<M>] luvun otsikko``).  The old parser keeps this as a
# continuation arm with an 8-token look-ahead window; we pin the recognizer's
# emitted node + span byte-identically to the node the old parser produces.
# ---------------------------------------------------------------------------


def _old_heading_placement_nodes(text: str):
    raw = tokenize(text)
    tokens, jolloin = apply_annotations_with_jolloin_pairs(raw)
    clause = surface_parse.parse(tokens, jolloin_renumber_pairs=jolloin or None)
    return [
        n
        for vg in clause.verb_groups
        for n in vg.nodes
        if isinstance(n, SurfaceHeadingPlacement)
    ]


def _anchor_num_pos(tokens) -> int:
    """The NUM token immediately governing a ``§:GEN`` (skipping a LETTER)."""
    for i in range(len(tokens) - 1):
        if tokens[i].cat != "NUM":
            continue
        j = i + 1
        if tokens[j].cat == "LETTER":
            j += 1
        if j < len(tokens) and tokens[j].cat == "PYKALA" and tokens[j].case == "GEN":
            return i
    raise AssertionError("no anchor NUM found")


def test_heading_edelle_luvun_otsikko_matches_old_parser():
    text = "lisätään lakiin uusi 53 a § ja 53 §:n edelle uusi luvun otsikko"
    tokens = _filtered(text)
    old_nodes = _old_heading_placement_nodes(text)
    assert len(old_nodes) == 1
    old = old_nodes[0]
    assert old.witness is not None
    assert old.witness.rule_id == "fi.heading_edelle_luvun_otsikko"

    entry = _anchor_num_pos(tokens)
    scan = _scan_at(tokens, entry)
    parsed = recognize_heading_edelle_luvun_otsikko(scan)
    assert parsed is not None
    assert parsed.form is HeadingForm.LUVUN_OTSIKKO
    assert heading_rule_id(parsed) == "fi.heading_edelle_luvun_otsikko"
    node = emit_headings_nodes(parsed)[0]
    assert node == old  # byte-identical: target_section, span, rule_id, ctx
    # Cursor advanced exactly to just past the matched OTSIKKO.
    assert scan.pos == old.witness.source_span[1]


def test_heading_edelle_luvun_otsikko_anchor_letter_suffix():
    text = "lisätään lakiin uusi 12 a § ja 12 a §:n edelle uusi luvun otsikko"
    tokens = _filtered(text)
    old = _old_heading_placement_nodes(text)[0]
    entry = _anchor_num_pos(tokens)
    scan = _scan_at(tokens, entry)
    parsed = recognize_heading_edelle_luvun_otsikko(scan)
    assert parsed is not None
    node = emit_headings_nodes(parsed)[0]
    assert node.target_section == "12a"
    assert node == old


def test_heading_edelle_luvun_otsikko_num_chapter_payload():
    # "uusi 5 luvun otsikko" — the payload chapter carries its own number.
    text = "lisätään lakiin uusi 4 § ja 4 §:n edelle uusi 5 luvun otsikko"
    tokens = _filtered(text)
    old = _old_heading_placement_nodes(text)[0]
    entry = _anchor_num_pos(tokens)
    scan = _scan_at(tokens, entry)
    parsed = recognize_heading_edelle_luvun_otsikko(scan)
    assert parsed is not None
    node = emit_headings_nodes(parsed)[0]
    assert node.target_section == "4"
    assert node == old


# ---------------------------------------------------------------------------
# Explicit preceding-heading target (``mukaanluettuna N §:n edellä oleva
# otsikko``) — emits SurfaceTargetRef (SECTION/HEADING facet) nodes, pinned
# byte-identically to the old ``_consume_including_preceding_heading_target``.
# ---------------------------------------------------------------------------


def _old_including_preceding_nodes(text: str):
    from lawvm.finland.johtolause.surface_parse import (
        Stream,
        _consume_including_preceding_heading_target,
    )

    tokens = _filtered(text)
    s = Stream(list(tokens))
    nodes = _consume_including_preceding_heading_target(s, "", part="")
    return s.pos, nodes


def test_including_preceding_single_matches_old_helper():
    text = "mukaanluettuna 5 §:n edellä oleva otsikko"
    tokens = _filtered(text)
    old_pos, old_nodes = _old_including_preceding_nodes(text)
    assert old_nodes is not None and len(old_nodes) == 1

    scan = _scan_at(tokens, 0)
    parsed = recognize_including_preceding_heading_target(scan)
    assert parsed is not None
    assert parsed.form is HeadingForm.INCLUDING_PRECEDING
    assert heading_rule_id(parsed) == "fi.including_preceding_heading_target"
    new_nodes = emit_headings_nodes(parsed)
    assert len(new_nodes) == 1
    node = new_nodes[0]
    assert isinstance(node, SurfaceTargetRef)
    assert node == old_nodes[0]
    assert scan.pos == old_pos


def test_including_preceding_range_expands_like_old_helper():
    text = "mukaanluettuna 5—7 §:n edellä oleva otsikko"
    tokens = _filtered(text)
    old_pos, old_nodes = _old_including_preceding_nodes(text)
    assert old_nodes is not None and len(old_nodes) == 3

    scan = _scan_at(tokens, 0)
    parsed = recognize_including_preceding_heading_target(scan)
    assert parsed is not None
    new_nodes = emit_headings_nodes(parsed)
    assert [n.label for n in new_nodes if isinstance(n, SurfaceTargetRef)] == ["5", "6", "7"]
    assert list(new_nodes) == list(old_nodes)
    assert scan.pos == old_pos


def test_including_preceding_letter_suffix_matches_old_helper():
    text = "mukaanluettuna 5 a §:n edellä oleva otsikko"
    tokens = _filtered(text)
    old_pos, old_nodes = _old_including_preceding_nodes(text)
    assert old_nodes is not None and len(old_nodes) == 1

    scan = _scan_at(tokens, 0)
    parsed = recognize_including_preceding_heading_target(scan)
    assert parsed is not None
    node = emit_headings_nodes(parsed)[0]
    assert isinstance(node, SurfaceTargetRef)
    assert node.label == "5a"
    assert node == old_nodes[0]
    assert scan.pos == old_pos


# ---------------------------------------------------------------------------
# Negative: a pure section reference is correctly DECLINED by every heading
# recognizer (None, cursor rewound).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "recognizer",
    [
        recognize_valiotsikko_ref,
        recognize_heading_after_uusi,
        recognize_trailing_heading_placement,
        recognize_heading_edelle_luvun_otsikko,
        recognize_including_preceding_heading_target,
    ],
)
def test_declines_plain_section_ref(recognizer):
    text = "Muutetaan 12 §:n 2 momentti."
    tokens = _filtered(text)
    sec_pos = next(i for i, t in enumerate(tokens) if t.cat == "NUM")
    scan = _scan_at(tokens, sec_pos)
    parsed = recognizer(scan)
    assert parsed is None
    # Cursor rewound (no partial consumption left dangling).
    assert scan.pos == sec_pos


def test_including_preceding_declines_without_mukaanluettuna():
    # A bare "N §:n edellä oleva otsikko" (no leading word) is NOT this rule.
    text = "5 §:n edellä oleva otsikko"
    tokens = _filtered(text)
    scan = _scan_at(tokens, 0)
    parsed = recognize_including_preceding_heading_target(scan)
    assert parsed is None
    assert scan.pos == 0
