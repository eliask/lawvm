"""parser — the slice-1 verb-group driver over the section-reference family.

A thin driver that reproduces ``surface_parse.parse`` for the
**section-reference-only** subset of the corpus, built on the ``sections``
recognizer family. For any clause whose shape falls outside that subset
(insertions, headings, backrefs, scope inheritance across verb groups, meta /
text-amend tails, jolloin, …) it raises :class:`OutOfScope` rather than
guessing — the differential gate only requires 0-delta on the in-scope subset.

Contract (the entry point the harness drives):

    parse(tokens, jolloin_renumber_pairs=None) -> SurfaceClause

It produces a real frozen ``SurfaceClause`` whose canonical form is
byte-identical to the old parser's on every in-scope clause:

  * ``verb_groups``      — one per amendment verb, with section-ref nodes
  * ``meta_clauses`` / ``text_amend_clauses`` / ``target_version_bindings`` — ()
  * ``source_text``      — " ".join(t.text for t in tokens if t.text)
  * ``consumed_count``   — the final cursor position, matching the old parser

Layering: this driver owns the verb-group loop, the token-stream control flow
(the lead-in / separator / trailing skips the old ``Stream`` did), and emission;
all within-phrase section structure lives in ``sections``. It does NOT do
cross-verb-group resolution (those clauses are out of scope and rejected).

The control-flow skips here are a faithful, narrowed port of the old parser's
section path: sentinel spans (statute-name / citation / provenance / reinst /
end-sentinel) and TEMPORAL markers are trivia around the operative section
refs; the rich separator absorbs provenance spans between list items; and a
trailing run of non-verb tokens after the last group is swallowed exactly as
the old outer loop's verb-seeking skip does, so ``consumed_count`` matches.
"""

from __future__ import annotations

from typing import Optional

from lawvm.finland.johtolause.grammar.combinators import Cursor
from lawvm.finland.johtolause.grammar.sections import (
    _Scan,
    _sep,
    _skip_sentinels,
    emit_section_nodes,
    extract_chapter,
    extract_part,
    recognize_pykala_prefix_section_ref,
    recognize_section_ref,
)
from lawvm.finland.johtolause.lexicon import Token
from lawvm.finland.johtolause.surface_model import (
    SurfaceClause,
    SurfaceNode,
    SurfaceVerbGroup,
    VerbKind,
)
from lawvm.finland.source_verb import SourceVerb


class OutOfScope(Exception):
    """Raised when a clause is not a pure section-reference clause.

    The slice-1 parser handles only the section-reference subset; the driver
    raises this (rather than silently mis-parsing) for any other shape so the
    validation script can catch-and-skip it.
    """


# ---------------------------------------------------------------------------
# Token-stream control flow (a narrowed port of the old Stream's idioms).
# ---------------------------------------------------------------------------


def _skip_cat(scan: _Scan, category: str) -> None:
    while (t := scan.peek()) and t.cat == category:
        scan.advance()


def _try_recognize_section(scan: _Scan, chapter: str, part: str) -> Optional[list[SurfaceNode]]:
    """Try the section-ref then prefix form at the cursor; None on no match.

    ``chapter`` / ``part`` are the intra-group inherited scope (empty at the
    first target); the emitter applies them where no explicit scope was parsed.
    """
    start = scan.pos
    parsed = recognize_section_ref(scan)
    if parsed is None:
        scan.goto(start)
        parsed = recognize_pykala_prefix_section_ref(scan)
    if parsed is None:
        scan.goto(start)
        return None
    return emit_section_nodes(parsed, chapter=chapter, part=part)


def _recognize_one_target(scan: _Scan, chapter: str = "", part: str = "") -> list[SurfaceNode]:
    """Recognize a single section-reference target at the cursor; emit nodes.

    Skips sentinel-span lead-in and an optional DOC:GEN ("lain 6, 7 ja 18 §"),
    then tries the section-ref form, then the genitive-plural prefix form
    (matching the old ``_target`` dispatch order for the section families).
    Raises :class:`OutOfScope` if no section reference is found.
    """
    _skip_sentinels(scan)

    # Optional DOC:GEN before structural targets (the old _target skips it).
    doc_saved = scan.pos
    t = scan.peek()
    if t and t.cat == "DOC" and t.case == "GEN":
        scan.advance()
        _skip_sentinels(scan)

    nodes = _try_recognize_section(scan, chapter, part)
    if nodes is not None:
        return nodes

    scan.goto(doc_saved)
    raise OutOfScope("not a section reference at target position")


def _parse_verb_group(scan: _Scan) -> tuple[Optional[SourceVerb], list[SurfaceNode]]:
    """Parse one verb group: VERB then a separator-joined section-ref list.

    Returns (verb_code, nodes). Raises :class:`OutOfScope` on any non-section
    shape inside the group.
    """
    t = scan.peek()
    if t is None or t.cat != "VERB":
        raise OutOfScope("expected verb at verb-group start")
    verb = t.verb_code
    scan.advance()

    _skip_sentinels(scan)
    _skip_cat(scan, "TEMPORAL")

    batch = _recognize_one_target(scan)
    nodes = list(batch)
    # Intra-group scope carry-forward: a later bare section list inherits the
    # preceding "N luvun" / "N osan" scope (the old parser threads this between
    # target batches in one verb group).
    chapter = extract_chapter(batch, "")
    part = extract_part(batch, "")

    while True:
        saved = scan.pos
        nxt = scan.peek()
        if nxt is None:
            break
        if nxt.cat == "VERB":
            break
        if _sep(scan) is None:
            # No separator and not a verb/end. The old parser ends the section
            # list here (its exotic continuation arms — heading residue, WORD
            # skips, anaphora — are all out of scope) and lets the outer loop
            # swallow the tail. Break and defer to the outer totality check: if
            # the tail is benign trailing trivia it is consumed to end; if it
            # was a target the old parser would have kept, the diff shows the
            # missing node as a delta (never a false 0-delta).
            scan.goto(saved)
            break
        after_sep = scan.peek()
        if after_sep is None or after_sep.cat == "VERB":
            # Trailing separator before a new verb group / end: the outer loop
            # owns this separator, so rewind and let the group end.
            scan.goto(saved)
            break
        try:
            more = _recognize_one_target(scan, chapter, part)
        except OutOfScope:
            # The separator led into a non-section continuation (a provenance
            # tail like ", viimemainittu niinkuin ...", a heading residue, …).
            # The old parser also stops the section list here and lets its outer
            # loop swallow the residue. Rewind to before the separator and break;
            # the outer totality skip consumes the tail to end (or the diff
            # records a genuinely dropped target as a delta — never a false pass).
            scan.goto(saved)
            break
        nodes.extend(more)
        chapter = extract_chapter(more, chapter)
        part = extract_part(more, part)

    return verb, nodes


def _has_later_verb(scan: _Scan) -> bool:
    toks = scan.cur.tokens
    return any(toks[i].cat == "VERB" for i in range(scan.pos, len(toks)))


def parse(
    tokens: list[Token],
    jolloin_renumber_pairs: dict[int, list[tuple[str, str, str]]] | None = None,
) -> SurfaceClause:
    """Parse a filtered token stream as a pure section-reference clause.

    Mirrors ``surface_parse.parse`` for the in-scope subset. Raises
    :class:`OutOfScope` for any clause outside it (including any that supplies
    ``jolloin_renumber_pairs`` — jolloin is out of scope for slice 1).
    """
    source_text = " ".join(t.text for t in tokens if t.text)

    if jolloin_renumber_pairs:
        raise OutOfScope("jolloin renumber pairs are out of scope for slice 1")

    scan = _Scan(Cursor(tokens))

    # Skip leading non-verb tokens (the old parser does the same).
    while not scan.cur.at_end and ((t := scan.peek()) is None or t.cat != "VERB"):
        scan.advance()

    if scan.cur.at_end:
        raise OutOfScope("no amendment verb (meta-only clause)")

    verb_groups: list[SurfaceVerbGroup] = []

    verb, nodes = _parse_verb_group(scan)
    if not nodes:
        raise OutOfScope("empty first verb group")
    verb_groups.append(
        SurfaceVerbGroup(verb=VerbKind.from_code(verb or SourceVerb.MUUTTAA), nodes=tuple(nodes))
    )

    # Subsequent verb groups, with the old outer loop's verb-seeking skip.
    while not scan.cur.at_end:
        saved = scan.pos
        _sep(scan)  # optional separator between groups
        if scan.cur.at_end:
            break
        cur = scan.peek()
        if cur is not None and cur.cat != "VERB":
            if _has_later_verb(scan):
                # Skip intervening non-verb tokens to the next verb group.
                while not scan.cur.at_end and ((tp := scan.peek()) is None or tp.cat != "VERB"):
                    scan.advance()
                if scan.cur.at_end:
                    break
            else:
                # No further verb: the trailing non-verb run is residue the old
                # outer loop swallows by advancing to end. Consume it so
                # consumed_count matches, then stop.
                while not scan.cur.at_end:
                    scan.advance()
                break
        verb2, nodes2 = _parse_verb_group(scan)
        if not nodes2:
            scan.goto(saved)
            break
        verb_groups.append(
            SurfaceVerbGroup(
                verb=VerbKind.from_code(verb2 or SourceVerb.MUUTTAA), nodes=tuple(nodes2)
            )
        )

    # Totality: the old parser's consumed_count is its final cursor position.
    # Any residual tail we did not account for means a shape the old parser
    # would have handled differently — treat as out of scope.
    if not scan.cur.at_end:
        tail = scan.peek()
        tail_cat = tail.cat if tail is not None else "END"
        raise OutOfScope(f"unconsumed tail at token {scan.pos} ({tail_cat})")

    return SurfaceClause(
        verb_groups=tuple(verb_groups),
        meta_clauses=(),
        text_amend_clauses=(),
        target_version_bindings=(),
        source_text=source_text,
        consumed_count=scan.pos,
    )
