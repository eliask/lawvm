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
from lawvm.finland.johtolause.grammar.insertions import (
    OutOfScopeInsertion,
    emit_insertion_nodes,
    insertion_rule_id,
    recognize_insertion,
)
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
    SurfaceInsertion,
    SurfaceNode,
    SurfaceVerbGroup,
    SurfaceWitness,
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


def _stamp_insertion_batch(
    nodes: list[SurfaceNode], start: int, end: int
) -> list[SurfaceNode]:
    """Stamp the shared batch witness on insertion nodes (old-parser semantics).

    The old ``_stamp_default_witness`` attaches one witness per insertion node
    whose span is the whole ``_target`` call ``(start, end)`` and whose rule_id
    is inferred from the node's shape. Reproduced here so the new parser's
    insertion witnesses are byte-identical.
    """
    out: list[SurfaceNode] = []
    for node in nodes:
        assert isinstance(node, SurfaceInsertion)
        out.append(
            SurfaceInsertion(
                kind=node.kind,
                label=node.label,
                chapter=node.chapter,
                part=node.part,
                sub_target=node.sub_target,
                witness=SurfaceWitness(rule_id=insertion_rule_id(node), source_span=(start, end)),
            )
        )
    return out


def _uusi_attached_to_current_target(scan: _Scan) -> bool:
    """True if a UUSI anchors THIS target (before any batch separator / verb).

    An ``uusi`` directly in the current target's operative span (no intervening
    list separator) marks the target as an insertion. When the insertion
    recognizer declines such a span (an out-of-scope insertion shape), the
    driver must NOT fall through to the section-reference recognizer — that
    would mis-read the insertion's authority/anchor section as an operative
    target (e.g. ``15 §:n 3 momentin nojalla uusi 8 a §``). The clause is out of
    scope instead.

    A UUSI that appears only AFTER a list separator (``…, sellaisena kuin se on
    uudistettuna …``) or in a later verb group is NOT attached to this target —
    the section-ref recognizer legitimately owns the leading targets, so the
    guard does not fire.
    """
    toks = scan.cur.tokens
    for i in range(scan.pos, len(toks)):
        cat = toks[i].cat
        if cat in ("VERB", "COMMA", "CONJ", "SEKA"):
            return False
        if cat == "UUSI":
            return True
    return False


def _try_recognize_target(
    scan: _Scan, batch_start: int, chapter: str, part: str
) -> Optional[tuple[list[SurfaceNode], bool]]:
    """Try insertion, then the section-ref / prefix forms at the cursor.

    Mirrors the old ``_target`` dispatch order (``_insertion`` before
    ``_section_ref``). Returns ``(nodes, is_insertion)`` or None when nothing
    matched. Insertion nodes get the batch witness spanning ``(batch_start,
    cursor)``; section nodes carry their own recognizer witness.

    Fail-loud: if the phrase carries an ``uusi`` anchor but the insertion
    recognizer declined it (an out-of-scope insertion shape), this raises
    :class:`OutOfScope` rather than mis-parsing the anchor section as a
    section reference.
    """
    start = scan.pos
    try:
        parsed_ins = recognize_insertion(scan, chapter, part)
    except OutOfScopeInsertion as exc:
        # The recogniser identified an out-of-scope insertion shape (e.g. a
        # ``… nojalla uusi 8 b`` bare-section authority insert). Decline the
        # whole clause rather than mis-read the authority list as a section ref.
        raise OutOfScope(str(exc)) from exc
    if parsed_ins is not None:
        nodes = emit_insertion_nodes(parsed_ins)
        return _stamp_insertion_batch(nodes, batch_start, scan.pos), True

    scan.goto(start)
    parsed = recognize_section_ref(scan)
    if parsed is None:
        scan.goto(start)
        parsed = recognize_pykala_prefix_section_ref(scan)
    if parsed is None:
        scan.goto(start)
        # Nothing parsed here. If an ``uusi`` anchors this very target (an
        # out-of-scope insertion shape the recognizer declined), reject loudly
        # rather than let the driver swallow it as benign residue and silently
        # drop the insertion the old parser would have emitted.
        if _uusi_attached_to_current_target(scan):
            raise OutOfScope("out-of-scope insertion shape (uusi anchor present)")
        return None
    return emit_section_nodes(parsed, chapter=chapter, part=part), False


def _recognize_one_target(
    scan: _Scan, chapter: str = "", part: str = ""
) -> tuple[list[SurfaceNode], bool]:
    """Recognize a single target (insertion or section ref); emit nodes.

    Records the batch start (the old ``_target`` entry, before its own
    sentinel skip — the witness anchor for insertions), skips sentinel-span
    lead-in and an optional DOC:GEN ("lain 6, 7 ja 18 §"), then tries the
    insertion form, then the section-ref form, then the genitive-plural prefix
    form (matching the old ``_target`` dispatch order). Returns
    ``(nodes, is_insertion)``. Raises :class:`OutOfScope` if no target is found.
    """
    batch_start = scan.pos
    _skip_sentinels(scan)

    # Optional DOC:GEN before structural targets (the old _target skips it).
    doc_saved = scan.pos
    t = scan.peek()
    if t and t.cat == "DOC" and t.case == "GEN":
        scan.advance()
        _skip_sentinels(scan)

    result = _try_recognize_target(scan, batch_start, chapter, part)
    if result is not None:
        return result

    scan.goto(doc_saved)
    raise OutOfScope("not a target at target position")


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

    batch, is_insertion = _recognize_one_target(scan)
    nodes = list(batch)
    # Intra-group scope carry-forward: a later bare section list inherits the
    # preceding "N luvun" / "N osan" scope (the old parser threads this between
    # target batches in one verb group).
    chapter = _extract_chapter(batch, "")
    part = _extract_part(batch, "")

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
            if is_insertion and not _tail_is_benign(scan):
                raise OutOfScope("undecodable insertion tail (no separator)")
            break
        after_sep = scan.peek()
        if after_sep is None or after_sep.cat == "VERB":
            # Trailing separator before a new verb group / end: the outer loop
            # owns this separator, so rewind and let the group end.
            scan.goto(saved)
            break
        try:
            more, more_is_insertion = _recognize_one_target(scan, chapter, part)
        except OutOfScope:
            # The separator led into a continuation this driver cannot parse.
            # For an insertion verb group the old parser would have kept folding
            # the residue into the same target list (chained ``sekä uusi …``,
            # postfix-chapter, heading arms), so letting the outer loop silently
            # swallow it would DROP nodes the old parser emits — a false pass.
            # Reject loudly instead. (For a section-ref group the residue is
            # genuinely benign trailing trivia the old outer loop also skips.)
            scan.goto(saved)
            if is_insertion and not _tail_is_benign(scan):
                raise OutOfScope("undecodable insertion continuation")
            break
        # Mixing insertion and section-ref batches inside one verb group is an
        # out-of-scope shape: the old parser threads scope/anaphora across them
        # in ways this driver does not reproduce. Reject loudly rather than
        # emit a divergent grouping.
        if is_insertion != more_is_insertion:
            raise OutOfScope("mixed insertion/section-ref continuation in verb group")
        nodes.extend(more)
        chapter = _extract_chapter(more, chapter)
        part = _extract_part(more, part)

    return verb, nodes


def _extract_chapter(nodes: list[SurfaceNode], current: str) -> str:
    """Chapter scope carried forward from a batch (section- and insertion-aware).

    A faithful narrowing of ``surface_parse._extract_chapter_from_nodes`` for the
    node types these two families emit: the section-family extractor handles
    scope blocks / coordination / section targets; an insertion's ``chapter``
    field also propagates to a following bare batch in the same verb group.
    """
    for node in reversed(nodes):
        if isinstance(node, SurfaceInsertion) and node.chapter:
            return node.chapter
    return extract_chapter(nodes, current)


def _extract_part(nodes: list[SurfaceNode], current: str) -> str:
    """Part scope carried forward from a batch (section- and insertion-aware)."""
    for node in reversed(nodes):
        if isinstance(node, SurfaceInsertion) and node.part:
            return node.part
    return extract_part(nodes, current)


def _has_later_verb(scan: _Scan) -> bool:
    toks = scan.cur.tokens
    return any(toks[i].cat == "VERB" for i in range(scan.pos, len(toks)))


# Token categories that may benignly trail an insertion batch (separators and
# sentinel spans the old parser also swallows without emitting a node).
_BENIGN_TAIL_CATS = frozenset(
    {
        "COMMA",
        "CONJ",
        "DASH",
        "SEKA",
        "TEMPORAL",
        "END_SENTINEL_SPAN",
        "CITATION_SPAN",
        "STATUTE_NAME_SPAN",
        "PROVENANCE_SPAN",
        "REINST_SPAN",
        "JOLLOIN_MOVE",
        "VALIOTSIKKO",
    }
)


def _tail_is_benign(scan: _Scan) -> bool:
    """True if everything up to the next VERB / end is benign trailing trivia.

    Used to decide whether an undecodable insertion continuation is a genuine
    out-of-scope shape (real content the old parser would have emitted) or
    merely trailing punctuation / sentinel spans the old outer loop swallows.
    """
    toks = scan.cur.tokens
    for i in range(scan.pos, len(toks)):
        if toks[i].cat == "VERB":
            return True
        if toks[i].cat not in _BENIGN_TAIL_CATS:
            return False
    return True


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
