"""moves — the SIIRTAA (move / renumber) recognizer family of the rewrite.

The move family covers the ``siirretään`` verb-group phenomena the old
``surface_parse.py`` recognizes around a move verb:

  * cross-verb move retarget — ``siirretään muutettu 85 b § 9 lukuun`` — a move
    that retargets a section established by a *preceding* verb group. The
    recognizer reads only the local syntax (``[muutettu] N § M lukuun``); the
    actual retargeting of the prior group's node is cross-verb-group resolution
    and is deferred (the resolver scans preceding groups for the source label).
  * direct relabel from context — ``joka siirretään [N luvun] M §:ksi`` — a
    relabel whose *source* section is the preceding context; the recognizer
    reads the destination (``[N luvun] M §:ksi``) only.
  * leading destination part — the ``I osaan,`` prefix that opens a move group
    and supplies the destination part for the targets that follow.
  * inline move tail — the ``, jotka samalla siirretään N lukuun`` / ``I osaan``
    consequence tail that retags an immediately preceding section batch with a
    move destination (realized on the target ref via
    ``move_clause_target_unit_kind``, exactly as the old parser does — there is
    no standalone ``SurfaceMoveTail`` node in the old output).

Two enforced layers (per the rewrite contract):

  * LOUD recognizers — pure functions over a ``_Scan`` cursor returning a
    structured intermediate (``ParsedMove``) carrying spans + raw fields, NO
    resolution and NO frozen-node construction.
  * thin emitters — turn the intermediate into the frozen ``Surface*`` nodes
    with witnesses, byte-identical to the old parser's construction.

Witness ``rule_id``s emitted here (the closed set for this family):
``fi.cross_verb_move_retarget``, ``fi.direct_section_relabel``.

Explicitly OUT OF SCOPE here (decline / never emit — these are driver- or
context-level, not a context-free move recognizer's job):

  * the ``jolloin`` renumber-pair consequence form (``jolloin nykyinen N
    momentti siirtyy M momentiksi``) — fed to the driver as
    ``jolloin_renumber_pairs`` and emitted as ``SurfaceRenumberTail`` by the
    driver, never by this recognizer.
  * cross-verb-group *resolution* (binding a retarget / relabel to the actual
    prior section) — the resolver's job.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from lawvm.core.target_scope import TargetUnitKind
from lawvm.finland.johtolause.grammar.combinators import Span
from lawvm.finland.johtolause.grammar.sections import (
    NumSuffix,
    _Scan,
    _chapter_ctx,
    _number_list,
    _number_with_suffix,
)
from lawvm.finland.johtolause.lexicon import Token
from lawvm.finland.johtolause.surface_model import (
    SurfaceCrossVerbMoveTail,
    SurfaceMoveTail,
    SurfaceNode,
    SurfaceRelabelFromContext,
    SurfaceTargetRef,
    SurfaceWitness,
    TargetKind,
)
from lawvm.finland.source_verb import SourceVerb

# ---------------------------------------------------------------------------
# Recognized move intermediates (the structures the emitters consume).
# ---------------------------------------------------------------------------


class MoveForm(Enum):
    """Which move-family production matched."""

    CROSS_VERB = "cross_verb"  # [muutettu] N § M lukuun
    RELABEL = "relabel"  # [N luvun] M §:ksi
    INLINE_TAIL = "inline_tail"  # , jotka samalla siirretään N lukuun / I osaan
    LEADING_PART = "leading_part"  # I osaan, (move-group destination prefix)


@dataclass(frozen=True, slots=True)
class ParsedMove:
    """A recognized move-family phrase: spans + raw labels.

    Architecture-neutral: carries only what the recognizer saw, plus the span.
    The emitter turns this into frozen ``Surface*`` nodes (or, for the inline
    tail / leading part, supplies the destination carrier a driver applies to a
    preceding batch — there is no standalone node for those forms in the old
    output).
    """

    form: MoveForm
    span: Span
    source_section_label: str = ""  # CROSS_VERB: the section to retarget
    destination_chapter: str = ""  # CROSS_VERB / INLINE_TAIL chapter destination
    destination_part: str = ""  # INLINE_TAIL / LEADING_PART part destination
    destination_label: str = ""  # RELABEL: the destination section label
    relabel_chapter: str = ""  # RELABEL: explicit destination chapter, if any


# ---------------------------------------------------------------------------
# Atomic token reads not already provided by ``sections`` (move-family local).
# ---------------------------------------------------------------------------


def _is_word(scan: _Scan, lemma: str) -> bool:
    t = scan.peek()
    return bool(t and t.cat == "WORD" and t.lemma == lemma)


def _is_siirtaa(token: Token) -> bool:
    """True iff a VERB token is the SIIRTAA (siirretään) amendment verb."""
    return token.verb_code == SourceVerb.SIIRTAA


def _section_label(scan: _Scan) -> Optional[str]:
    """Read ``N [letter]`` and join into a section label (e.g. '85b')."""
    ns: Optional[NumSuffix] = _number_with_suffix(scan)
    if ns is None:
        return None
    return ns[0] + ns[1]


# ---------------------------------------------------------------------------
# Recognizers (faithful ports of the old context-free move helpers).
# ---------------------------------------------------------------------------


def recognize_cross_verb_move_tail(scan: _Scan) -> Optional[ParsedMove]:
    """Recognize ``[muutettu] N § [,] M lukuun`` (cross-verb move retarget).

    Mirrors ``surface_parse._parse_cross_verb_move_tail``. Returns a
    ``ParsedMove`` (the intermediate) or None (rewinding ``scan``). Resolution
    of *which* prior section ``N`` denotes is deferred to the resolver.
    """
    start = scan.pos
    if _is_word(scan, "muutettu"):
        scan.advance()

    source_label = _section_label(scan)
    if source_label is None:
        scan.goto(start)
        return None
    t = scan.peek()
    if not (t and t.cat == "PYKALA" and t.case != "ILL"):
        scan.goto(start)
        return None
    scan.advance()  # consume §

    if (t := scan.peek()) and t.cat == "COMMA":
        scan.advance()

    dest_chapter = _section_label(scan)
    if dest_chapter is None:
        scan.goto(start)
        return None
    t = scan.peek()
    if not (t and t.cat == "LUKU" and t.case == "ILL"):
        scan.goto(start)
        return None
    scan.advance()  # consume LUKU:ILL

    return ParsedMove(
        form=MoveForm.CROSS_VERB,
        span=Span(start, scan.pos),
        source_section_label=source_label,
        destination_chapter=dest_chapter,
    )


def recognize_relabel_from_context(scan: _Scan) -> Optional[ParsedMove]:
    """Recognize ``[N luvun] M §:ksi`` (direct relabel from context).

    Mirrors ``surface_parse._parse_relabel_from_context``. The source section
    is the preceding context and is NOT read here (deferred to the resolver).
    """
    start = scan.pos
    dest_chapter = _chapter_ctx(scan) or ""
    dest_num: Optional[NumSuffix] = _number_with_suffix(scan)
    if dest_num is None:
        scan.goto(start)
        return None
    t = scan.peek()
    if not (t and t.cat == "PYKALA" and ":ksi" in (t.text or "").lower()):
        scan.goto(start)
        return None
    scan.advance()  # consume §:ksi

    return ParsedMove(
        form=MoveForm.RELABEL,
        span=Span(start, scan.pos),
        destination_label=dest_num[0] + dest_num[1],
        relabel_chapter=dest_chapter,
    )


def recognize_leading_move_destination_part(scan: _Scan) -> Optional[ParsedMove]:
    """Recognize a leading ``N osaan [,]`` move-group destination-part prefix.

    Mirrors ``surface_parse._leading_move_destination_part``. Faithful quirk: a
    single number that is NOT followed by ``osaan`` (OSA:ILL) leaves the scanner
    ADVANCED past the number list and returns None (the old helper returns ``""``
    after consuming the list and only rewinds when the OSA test fails); callers
    that need to retry from the original position must save/restore themselves.
    """
    start = scan.pos
    nums = _number_list(scan)
    if not nums or len(nums) != 1:
        # Old helper: no rewind here (returns "" with the list consumed).
        return None
    t = scan.peek()
    if not (t and t.cat == "OSA" and t.case == "ILL"):
        scan.goto(start)
        return None
    scan.advance()  # consume OSA:ILL
    if (t := scan.peek()) and t.cat == "COMMA":
        scan.advance()
    return ParsedMove(
        form=MoveForm.LEADING_PART,
        span=Span(start, scan.pos),
        destination_part=nums[0][0] + nums[0][1],
    )


def recognize_inline_move_tail(scan: _Scan) -> Optional[ParsedMove]:
    """Recognize ``[,] [conj] jotka samalla siirretään [spans] N lukuun / N osaan``.

    Mirrors ``surface_parse._inline_move_clause_tail_destination`` (the
    destination-carrier extraction; the batch retagging it performs is driver /
    emitter work applied to the preceding section batch). Returns a
    ``ParsedMove`` whose ``destination_chapter`` xor ``destination_part`` is set,
    or None (rewinding ``scan``).
    """
    start = scan.pos

    if (t := scan.peek()) and t.cat == "CONJ":
        scan.advance()
    if (t := scan.peek()) and t.cat == "COMMA":
        scan.advance()
    while (t := scan.peek()) and t.cat == "WORD":
        scan.advance()

    t = scan.peek()
    if not (t and t.cat == "VERB" and _is_siirtaa(t)):
        scan.goto(start)
        return None
    scan.advance()  # consume siirretään

    # Anaphoric span / pronoun residue between the verb and the destination.
    while (t := scan.peek()) and (
        t.cat == "STATUTE_NAME_SPAN"
        or (
            t.cat == "WORD"
            and (t.lemma in {"se", "ne"} or (t.text or "").lower() in {"se", "ne"})
        )
    ):
        scan.advance()

    if (t := scan.peek()) and t.cat == "DOC" and t.case == "ILL":
        scan.advance()
        while (t := scan.peek()) and t.cat == "WORD":
            scan.advance()

    nums = _number_list(scan)
    if not nums or len(nums) != 1:
        scan.goto(start)
        return None
    t = scan.peek()
    if t is None:
        scan.goto(start)
        return None
    dest_label = nums[0][0] + nums[0][1]
    if t.cat == "LUKU" and t.case == "ILL":
        scan.advance()
        return ParsedMove(
            form=MoveForm.INLINE_TAIL,
            span=Span(start, scan.pos),
            destination_chapter=dest_label,
        )
    if t.cat == "OSA" and t.case == "ILL":
        scan.advance()
        return ParsedMove(
            form=MoveForm.INLINE_TAIL,
            span=Span(start, scan.pos),
            destination_part=dest_label,
        )
    scan.goto(start)
    return None


# ---------------------------------------------------------------------------
# Emitters — ParsedMove -> frozen Surface* nodes (faithful to the old parser).
# ---------------------------------------------------------------------------


def emit_cross_verb_move_nodes(parsed: ParsedMove) -> list[SurfaceNode]:
    """Emit the cross-verb move retarget node.

    Faithful to ``_parse_cross_verb_move_tail``: a single
    ``SurfaceCrossVerbMoveTail`` carrying the source label + destination chapter,
    with ``move_clause_target_unit_kind='chapter'``.
    """
    w = SurfaceWitness(
        rule_id="fi.cross_verb_move_retarget",
        source_span=(parsed.span.start, parsed.span.end),
    )
    return [
        SurfaceCrossVerbMoveTail(
            source_section_label=parsed.source_section_label,
            destination_chapter=parsed.destination_chapter,
            witness=w,
            move_clause_target_unit_kind="chapter",
        )
    ]


def emit_relabel_nodes(parsed: ParsedMove) -> list[SurfaceNode]:
    """Emit the direct relabel node (faithful to ``_parse_relabel_from_context``)."""
    w = SurfaceWitness(
        rule_id="fi.direct_section_relabel",
        source_span=(parsed.span.start, parsed.span.end),
    )
    return [
        SurfaceRelabelFromContext(
            destination_label=parsed.destination_label,
            destination_chapter=parsed.relabel_chapter,
            witness=w,
        )
    ]


def emit_move_tail_node(parsed: ParsedMove) -> SurfaceMoveTail:
    """Build a standalone ``SurfaceMoveTail`` for an inline-tail destination.

    Note: the OLD parser does NOT emit a standalone ``SurfaceMoveTail`` — it
    realizes a move-to-chapter/part by retagging the preceding section batch's
    ``SurfaceTargetRef`` nodes via :func:`retag_moved_targets`. This builder is
    provided for callers (e.g. the future discourse transducer) that want the
    move destination as an explicit node; it is NOT used to reproduce the old
    output's byte form.
    """
    kind: Optional[TargetUnitKind] = (
        "chapter" if parsed.destination_chapter else "part" if parsed.destination_part else None
    )
    return SurfaceMoveTail(
        destination_chapter=parsed.destination_chapter,
        destination_part=parsed.destination_part,
        witness=SurfaceWitness(
            rule_id="fi.move_tail", source_span=(parsed.span.start, parsed.span.end)
        ),
        move_clause_target_unit_kind=kind,
    )


def _is_whole_section_target(node: SurfaceTargetRef) -> bool:
    """A whole-section target ref (no sub-ref / facet narrowing).

    Mirrors ``surface_parse._is_whole_target`` for the section case: a section
    target with no sub-refs, or whose sub-refs are all whole-section markers.
    """
    if node.kind != TargetKind.SECTION:
        return False
    if not node.sub_refs:
        return True
    return all(
        sr.momentti == 0 and not sr.item and sr.facet is None for sr in node.sub_refs
    )


def retag_moved_targets(
    all_nodes: list[SurfaceNode],
    batch_nodes: list[SurfaceNode],
    parsed: ParsedMove,
) -> None:
    """Retag the preceding section batch with an inline move-tail destination.

    Faithful port of ``surface_parse._tag_inline_move_clause_target_batch``:
    mutates ``all_nodes`` in place, replacing the whole-section ``SurfaceTargetRef``
    nodes named in ``batch_nodes`` with copies carrying the destination
    chapter/part and ``move_clause_target_unit_kind``. This is the form the old
    parser's move-to-chapter/part output actually takes (no standalone node).
    """
    dest_chapter = parsed.destination_chapter
    dest_part = parsed.destination_part
    if not dest_chapter and not dest_part:
        return

    moved_labels: set[str] = set()
    batch_chapters: set[str] = set()
    batch_parts: set[str] = set()
    for node in batch_nodes:
        if isinstance(node, SurfaceTargetRef) and node.kind == TargetKind.SECTION:
            if _is_whole_section_target(node):
                moved_labels.add(node.label)
                if node.chapter:
                    batch_chapters.add(node.chapter)
                if node.part:
                    batch_parts.add(node.part)
    if not moved_labels:
        return

    for i, node in enumerate(all_nodes):
        if not isinstance(node, SurfaceTargetRef):
            continue
        if node.kind != TargetKind.SECTION or node.label not in moved_labels:
            continue
        if not _is_whole_section_target(node):
            continue
        if (
            node.chapter
            and batch_chapters
            and node.chapter not in batch_chapters
            and node.chapter != dest_chapter
        ):
            continue
        if (
            node.part
            and batch_parts
            and node.part not in batch_parts
            and node.part != dest_part
        ):
            continue

        new_chapter = dest_chapter if dest_chapter else node.chapter
        new_part = dest_part if dest_part and not node.part else node.part
        kind: Optional[TargetUnitKind] = (
            "chapter" if dest_chapter else "part" if dest_part else None
        )
        all_nodes[i] = SurfaceTargetRef(
            kind=node.kind,
            label=node.label,
            chapter=new_chapter,
            part=new_part,
            sub_refs=node.sub_refs,
            notes=node.notes,
            move_clause_target_unit_kind=kind,
            renumber_dest=node.renumber_dest,
            renumber_dest_chapter=node.renumber_dest_chapter,
            renumber_dest_part=node.renumber_dest_part,
            witness=node.witness,
        )


def emit_moves_nodes(parsed: ParsedMove) -> list[SurfaceNode]:
    """Turn a recognized ``ParsedMove`` into frozen Surface* nodes.

    Only the standalone forms (cross-verb retarget, relabel) produce nodes. The
    inline-tail and leading-part forms do not stand alone in the old output
    (they retag a preceding batch); for those this returns ``[]`` and callers
    use :func:`retag_moved_targets` / the leading-part destination directly.
    """
    if parsed.form is MoveForm.CROSS_VERB:
        return emit_cross_verb_move_nodes(parsed)
    if parsed.form is MoveForm.RELABEL:
        return emit_relabel_nodes(parsed)
    return []
