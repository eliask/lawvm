"""Worked-example tests for the container recognizer family.

Each test asserts byte-identity to the OLD parser (``surface_parse.parse``) via
``grammar.diff.compare_surface_parsers``: the comparison is objective, not
self-referential — a single container clause is fed through both the old parser
and a small local driver dispatching to ``recognize_containers`` /
``emit_containers_nodes``, and the full canonical models are diffed.

A local driver lives here (mirroring ``grammar/parser.py``'s verb-group loop,
narrowed to the container family) so the tests exercise the recognizer + emitter
end-to-end without depending on the shared slice-1 section driver.
"""

from __future__ import annotations

from typing import Optional

import pytest

from lawvm.finland.johtolause import surface_parse
from lawvm.finland.johtolause.grammar.combinators import Cursor
from lawvm.finland.johtolause.grammar.containers import (
    emit_containers_nodes,
    recognize_containers,
)
from lawvm.finland.johtolause.grammar.diff import compare_surface_parsers
from lawvm.finland.johtolause.grammar.sections import _Scan, _sep, _skip_sentinels
from lawvm.finland.johtolause.lexer import Token
from lawvm.finland.johtolause.surface_model import (
    SurfaceClause,
    SurfaceNode,
    SurfaceTargetRef,
    SurfaceVerbGroup,
    VerbKind,
)
from lawvm.finland.source_verb import SourceVerb


class _OutOfScope(Exception):
    """The local test driver declines a non-container clause (fail-loud)."""


def _skip_cat(scan: _Scan, category: str) -> None:
    while (t := scan.peek()) and t.cat == category:
        scan.advance()


def _recognize_one_target(scan: _Scan, part: str) -> list[SurfaceNode]:
    _skip_sentinels(scan)
    doc_saved = scan.pos
    t = scan.peek()
    if t and t.cat == "DOC" and t.case == "GEN":
        scan.advance()
        _skip_sentinels(scan)
    parsed = recognize_containers(scan, part=part)
    if parsed is not None:
        return emit_containers_nodes(parsed)
    scan.goto(doc_saved)
    raise _OutOfScope("not a container reference at target position")


def _extract_part(nodes: list[SurfaceNode], current: str) -> str:
    for node in reversed(nodes):
        if isinstance(node, SurfaceTargetRef) and node.part:
            return node.part
    return current


def _parse_verb_group(scan: _Scan) -> tuple[Optional[SourceVerb], list[SurfaceNode]]:
    t = scan.peek()
    if t is None or t.cat != "VERB":
        raise _OutOfScope("expected verb at verb-group start")
    verb = t.verb_code
    scan.advance()
    _skip_sentinels(scan)
    _skip_cat(scan, "TEMPORAL")

    batch = _recognize_one_target(scan, "")
    nodes = list(batch)
    part = _extract_part(batch, "")

    while True:
        saved = scan.pos
        nxt = scan.peek()
        if nxt is None or nxt.cat == "VERB":
            break
        if _sep(scan) is None:
            if scan.pos != saved:
                try:
                    more = _recognize_one_target(scan, part)
                except _OutOfScope:
                    scan.goto(saved)
                    break
                nodes.extend(more)
                part = _extract_part(more, part)
                continue
            scan.goto(saved)
            break
        after_sep = scan.peek()
        if after_sep is None or after_sep.cat == "VERB":
            scan.goto(saved)
            break
        try:
            more = _recognize_one_target(scan, part)
        except _OutOfScope:
            scan.goto(saved)
            break
        nodes.extend(more)
        part = _extract_part(more, part)

    return verb, nodes


def _local_parse(
    tokens: list[Token],
    jolloin_renumber_pairs: dict | None = None,
) -> SurfaceClause:
    """A narrowed container-family driver for the worked-example clauses."""
    source_text = " ".join(t.text for t in tokens if t.text)
    if jolloin_renumber_pairs:
        raise _OutOfScope("jolloin renumber pairs are out of scope")

    scan = _Scan(Cursor(tokens))
    while not scan.cur.at_end and ((t := scan.peek()) is None or t.cat != "VERB"):
        scan.advance()
    if scan.cur.at_end:
        raise _OutOfScope("no amendment verb")

    verb_groups: list[SurfaceVerbGroup] = []
    verb, nodes = _parse_verb_group(scan)
    if not nodes:
        raise _OutOfScope("empty first verb group")
    verb_groups.append(
        SurfaceVerbGroup(verb=VerbKind.from_code(verb or SourceVerb.MUUTTAA), nodes=tuple(nodes))
    )

    if not scan.cur.at_end:
        tail = scan.peek()
        tail_cat = tail.cat if tail is not None else "END"
        raise _OutOfScope(f"unconsumed tail ({tail_cat})")

    return SurfaceClause(
        verb_groups=tuple(verb_groups),
        meta_clauses=(),
        text_amend_clauses=(),
        target_version_bindings=(),
        source_text=source_text,
        consumed_count=scan.pos,
    )


# ---------------------------------------------------------------------------
# Positive worked examples — each must be byte-identical to the old parser.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Muutetaan 3 luku",  # chapter, single
        "Kumotaan 3 ja 4 luku",  # chapter, coordinated list
        "Muutetaan 5 a luku",  # chapter, number+suffix
        "Muutetaan 3 luvun otsikko",  # chapter heading facet
        "Muutetaan luku 3",  # reversed chapter (fi.chapter_ref_reversed)
        "Muutetaan II osa",  # part, roman label
        "Muutetaan II osan 3 luku",  # part-scoped chapter target
        "Muutetaan lain nimike",  # nimike (statute title)
        "Muutetaan liite",  # appendix, bare
        "Muutetaan liite 1",  # appendix, numbered (trailing)
        "Kumotaan liitteet 1 ja 2",  # appendix, coordinated list
    ],
)
def test_container_byte_identical_to_old(text: str) -> None:
    report = compare_surface_parsers(text, surface_parse.parse, _local_parse)
    assert report.equal, report.summary()


# ---------------------------------------------------------------------------
# Negative — a bare-section clause is the section family's job; the container
# family must decline it (fail-loud), never miscompile it.
# ---------------------------------------------------------------------------


def test_bare_section_clause_declined() -> None:
    from lawvm.finland.johtolause.grammar.diff import parse_text_with

    with pytest.raises(_OutOfScope):
        parse_text_with("Muutetaan 12 §", _local_parse)


def test_section_with_subref_declined() -> None:
    from lawvm.finland.johtolause.grammar.diff import parse_text_with

    with pytest.raises(_OutOfScope):
        parse_text_with("muutetaan 5 §:n 2 momentti", _local_parse)
