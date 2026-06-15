"""Byte-identity tests for the back-reference / anaphora recognizer family.

The objective gate is byte-identity to the OLD parser (``surface_parse.parse``)
on the shapes this family emits a ``SurfaceBackRef`` for.  A ``SurfaceBackRef`` is
only ever emitted as a CONTINUATION arm after a complete prior target batch (the
old parser does not read a leading bare ``mainitun pykälän …`` as a target — such
a clause parses to an empty verb group), so each test drives the recognizer from
the ``BACKREF`` token the old parser reached *after* consuming the separator,
folds that separator into the witness-span start exactly as the driver's
``_try_valiotsikko`` does, and compares the emitted node to the
``SurfaceBackRef`` the OLD parser produced for the same clause — byte-identical
(arity, sub-refs, witness rule_id + span).

A negative test confirms the recognizer DECLINES a plain section reference and a
provenance anaphor (``mainitussa … asetuksessa``) without partial consumption.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from lawvm.finland.johtolause import surface_parse
from lawvm.finland.johtolause.grammar.backrefs import (
    BackRefForm,
    ParsedBackRef,
    backref_rule_id,
    emit_backref_nodes,
    recognize_backref,
)
from lawvm.finland.johtolause.grammar.combinators import Cursor, Span
from lawvm.finland.johtolause.grammar.sections import SubRef, _Scan
from lawvm.finland.johtolause.lexer import tokenize
from lawvm.finland.johtolause.scan import apply_annotations_with_jolloin_pairs
from lawvm.finland.johtolause.surface_model import (
    BackRefArity,
    FacetKind,
    SurfaceBackRef,
    SurfaceSubRef,
)


def _filtered(text: str):
    raw = tokenize(text)
    tokens, _ = apply_annotations_with_jolloin_pairs(raw)
    return tokens


def _scan_at(tokens, pos: int) -> _Scan:
    return _Scan(Cursor(tokens, pos))


def _old_backref_node(text: str) -> SurfaceBackRef:
    """The single ``SurfaceBackRef`` the OLD parser emits for ``text``."""
    raw = tokenize(text)
    tokens, jolloin = apply_annotations_with_jolloin_pairs(raw)
    clause = surface_parse.parse(tokens, jolloin_renumber_pairs=jolloin or None)
    out = [
        n
        for vg in clause.verb_groups
        for n in vg.nodes
        if isinstance(n, SurfaceBackRef)
    ]
    assert len(out) == 1, f"expected exactly one SurfaceBackRef for {text!r}"
    return out[0]


def _recognize_with_folded_sep(text: str) -> SurfaceBackRef:
    """Drive the recognizer from the BACKREF; fold the preceding separator in.

    Reproduces the driver context: the loop consumes the separator before the
    BACKREF, then the recognizer runs; the witness span START is the separator
    position (the loop-iteration ``saved``), so we rewrite the span exactly as
    the driver's ``_try_valiotsikko`` wrapper does.
    """
    tokens = _filtered(text)
    br_pos = next(i for i, t in enumerate(tokens) if t.cat == "BACKREF")
    sep_start = br_pos - 1  # the CONJ/COMMA the driver swallowed before the anaphor
    scan = _scan_at(tokens, br_pos)
    parsed = recognize_backref(scan)
    assert parsed is not None
    parsed = replace(parsed, span=Span(sep_start, parsed.span.end))
    nodes = emit_backref_nodes(parsed)
    assert len(nodes) == 1
    node = nodes[0]
    assert isinstance(node, SurfaceBackRef)
    return node


# ---------------------------------------------------------------------------
# Byte-identity against the OLD parser, every reproducible shape.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # Singular, momentti sub-ref (the spec's worked example).
        "Kumotaan 3 § ja mainitun pykälän 2 momentti",
        # Singular, whole section (nominative "mainittu pykälä").
        "Kumotaan 7 § ja mainittu pykälä",
        # Singular, heading facet.
        "Muutetaan 5 § ja mainitun pykälän otsikko",
        # Singular, two sub-refs (heading + momentti), comma- then conj-led.
        "Muutetaan 5 § sekä mainitun pykälän otsikko ja 1 momentti",
        # Plural, heading facet (the spec's worked example).
        "Kumotaan 3 ja 4 § sekä mainittujen pykälien otsikot",
        # Singular under a chapter scope block before the backref.
        "Muutetaan 2 luvun 3 § ja mainitun pykälän 1 momentti",
        # Singular, kohta sub-ref.
        "Muutetaan 4 § ja mainitun pykälän 2 momentin 1 kohta",
        # Plural, momentti sub-ref.
        "Muutetaan 1 ja 2 § sekä mainittujen pykälien 1 momentti",
        # Comma-led continuation.
        "Kumotaan 9 §, mainitun pykälän 3 momentti",
    ],
)
def test_backref_recognizer_byte_identical_to_old_parser(text):
    node = _recognize_with_folded_sep(text)
    old = _old_backref_node(text)
    assert node == old  # arity, sub_refs, witness rule_id + span all identical


# ---------------------------------------------------------------------------
# Arity + sub-ref + rule_id spot checks on the spec's two worked examples.
# ---------------------------------------------------------------------------


def test_singular_momentti_subref_shape():
    text = "Kumotaan 3 § ja mainitun pykälän 2 momentti"
    tokens = _filtered(text)
    br_pos = next(i for i, t in enumerate(tokens) if t.cat == "BACKREF")
    parsed = recognize_backref(_scan_at(tokens, br_pos))
    assert parsed is not None
    assert parsed.form is BackRefForm.SINGULAR
    assert backref_rule_id(parsed) == "fi.backref_singular"
    node = emit_backref_nodes(parsed)[0]
    assert isinstance(node, SurfaceBackRef)
    assert node.referent_type is BackRefArity.SINGULAR
    assert node.sub_refs == (SurfaceSubRef(momentti=2, item="", facet=None, special=""),)


def test_plural_heading_subref_shape():
    text = "Kumotaan 3 ja 4 § sekä mainittujen pykälien otsikot"
    tokens = _filtered(text)
    br_pos = next(i for i, t in enumerate(tokens) if t.cat == "BACKREF")
    parsed = recognize_backref(_scan_at(tokens, br_pos))
    assert parsed is not None
    assert parsed.form is BackRefForm.PLURAL
    assert backref_rule_id(parsed) == "fi.backref_plural"
    node = emit_backref_nodes(parsed)[0]
    assert isinstance(node, SurfaceBackRef)
    assert node.referent_type is BackRefArity.PLURAL
    assert node.sub_refs == (
        SurfaceSubRef(momentti=0, item="", facet=FacetKind.HEADING, special="otsikko"),
    )


def test_emit_carries_no_scope_context():
    """A SurfaceBackRef is scope-free (resolution deferred); ctx args are inert."""
    parsed = ParsedBackRef(
        form=BackRefForm.SINGULAR, span=Span(0, 3), subs=(SubRef(momentti=2),)
    )
    node = emit_backref_nodes(parsed, chapter="3", part="II")[0]
    assert isinstance(node, SurfaceBackRef)
    assert node.referent_type is BackRefArity.SINGULAR
    assert node.sub_refs == (SurfaceSubRef(momentti=2, item="", facet=None, special=""),)


# ---------------------------------------------------------------------------
# Negative: a plain section ref and a provenance anaphor are DECLINED, cursor
# rewound (no partial consumption).
# ---------------------------------------------------------------------------


def test_declines_plain_section_ref():
    text = "Muutetaan 12 §:n 2 momentti."
    tokens = _filtered(text)
    sec_pos = next(i for i, t in enumerate(tokens) if t.cat == "NUM")
    scan = _scan_at(tokens, sec_pos)
    parsed = recognize_backref(scan)
    assert parsed is None
    assert scan.pos == sec_pos  # cursor rewound (it never started)


def test_declines_backref_not_followed_by_pykala():
    """A BACKREF determiner not followed by PYKALA is declined, cursor rewound.

    ``mainitun lain 5 §`` is a document anaphor (``BACKREF DOC …``), NOT the
    ``BACKREF PYKALA`` structural-backref shape; the recognizer must back out
    fully (cursor rewound to the BACKREF) so the driver routes it elsewhere
    instead of miscompiling it as a ``SurfaceBackRef``.
    """
    text = "Muutetaan mainitun lain 5 §"
    tokens = _filtered(text)
    br_pos = next(i for i, t in enumerate(tokens) if t.cat == "BACKREF")
    assert tokens[br_pos + 1].cat != "PYKALA"  # followed by DOC, not §
    scan = _scan_at(tokens, br_pos)
    parsed = recognize_backref(scan)
    assert parsed is None
    assert scan.pos == br_pos  # cursor rewound to the BACKREF, nothing consumed
