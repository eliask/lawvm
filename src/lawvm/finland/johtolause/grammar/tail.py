"""tail — the remaining single-owner weirdness-ledger recognizer family.

The "tail" of the FI johtolause grammar rewrite: the rare, single-owner witness
rules that the five workhorse families (sections / insertions / containers /
headings / moves) do not emit.  Each fires only a handful of times across the
whole corpus (weirdness-ledger fossils), and each is entangled — it co-occurs
inside a section/insert batch rather than forming a clean standalone clause — so
they are validated at the recognizer-helper level against the old parser's
helpers, not as a standalone clause subset.

Two rules are owned here (the remaining gap against the 34-rule contract after
the other five families landed):

  * ``fi.lukuun_ottamatta_exception`` — ``lukuun ottamatta (kuitenkaan)? <sec>``
    exception carve-out (``muutetaan 4-7 luku, lukuun ottamatta kuitenkaan 7
    luvun 73 §``).  Re-stamps the excepted section ref with ``is_exception=True``,
    a trailing ``"exception"`` note, and the exception witness.
  * ``fi.insertion_section_postfix_chapter`` — an insertion whose destination
    chapter follows its section (``uusi 35 a § lukuun 5, 104 a § lukuun 6`` or
    ``uusi 35 c § 5 lukuun``).  Emits one ``SurfaceInsertion`` (SECTION) per
    (section, chapter) pair.

Both faithfully reproduce the old ``surface_parse`` helpers
(``_lukuun_ottamatta_exception``, ``_postfix_chapter_section_inserts``); the diff
harness arbitrates byte-identity.

Two enforced layers (per the rewrite contract):

  * LOUD recognizers — pure functions over a ``_Scan`` cursor returning a
    structured intermediate carrying spans + raw fields; no frozen-node
    construction.
  * thin emitters turning the intermediate into the frozen nodes.

Witness ``rule_id``s emitted here (the closed set for this family):
``fi.lukuun_ottamatta_exception``, ``fi.insertion_section_postfix_chapter``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional

from lawvm.finland.johtolause.grammar.combinators import Span, cat
from lawvm.finland.johtolause.grammar.sections import (
    NumSuffix,
    ParsedSection,
    _Scan,
    _expand_range_single,
    _number_list,
    _number_with_suffix,
    _read,
    _sep,
    emit_section_nodes,
    recognize_section_ref,
)
from lawvm.finland.johtolause.surface_model import (
    SurfaceDescendantCoordination,
    SurfaceInsertion,
    SurfaceNode,
    SurfaceScopeBlock,
    SurfaceTargetRef,
    SurfaceWitness,
    TargetKind,
)

# Combinator leaf matchers reused across this module's recognizers.
_UUSI = cat("UUSI")


# ===========================================================================
# Rule: fi.lukuun_ottamatta_exception
#
# Pattern:  LUKU:ILL  WORD("ottamatta")  [WORD("kuitenkaan")]?  <section_ref>
# Faithful port of surface_parse._lukuun_ottamatta_exception.  The excepted
# section ref is recognized by the section family; the emitter re-stamps each
# emitted node with is_exception=True, a trailing "exception" note, and the
# exception witness (which spans the whole "lukuun ottamatta … §" clause).
# ===========================================================================


@dataclass(frozen=True, slots=True)
class ParsedException:
    """A recognized ``lukuun ottamatta`` exception: span + the excepted ref."""

    span: Span
    section: ParsedSection


def recognize_exception(
    scan: _Scan, chapter: str = "", part: str = ""
) -> Optional[ParsedException]:
    """Recognize ``lukuun ottamatta (kuitenkaan)? <section_ref>``.

    Mirrors ``surface_parse._lukuun_ottamatta_exception``: requires LUKU:ILL
    ("lukuun") then the literal word "ottamatta", an optional "kuitenkaan", then
    a section reference (recognized by the section family).  Returns None
    (rewinding ``scan``) on no match.  ``chapter`` / ``part`` are unused at
    recognition time (the inner ref carries its own explicit scope) but kept on
    the signature for parity with the family contract.
    """
    start = scan.pos
    t = scan.peek()
    if not (t and t.cat == "LUKU" and t.case == "ILL"):
        return None
    scan.advance()  # consume "lukuun"
    t = scan.peek()
    if not (t and t.cat == "WORD" and t.text.lower() == "ottamatta"):
        scan.goto(start)
        return None
    scan.advance()  # consume "ottamatta"
    if (t := scan.peek()) and t.cat == "WORD" and t.text.lower() == "kuitenkaan":
        scan.advance()
    parsed = recognize_section_ref(scan)
    if parsed is None:
        scan.goto(start)
        return None
    return ParsedException(span=Span(start, scan.pos), section=parsed)


def _stamp_exception(node: SurfaceNode, witness: SurfaceWitness) -> SurfaceNode:
    """Re-stamp a section node as an exception target (faithful re-mint).

    Mirrors the old parser's per-node re-construction: set ``is_exception=True``,
    append the ``"exception"`` note, and replace the witness — for plain target
    refs, for the targets inside a scope block, and for a descendant-coordination
    base.  Other node kinds are left as-is (the old parser only touches these
    three).
    """
    if isinstance(node, SurfaceTargetRef):
        return replace(
            node,
            notes=node.notes + ("exception",),
            is_exception=True,
            witness=witness,
        )
    if isinstance(node, SurfaceScopeBlock):
        new_targets = tuple(
            replace(
                t,
                notes=t.notes + ("exception",),
                is_exception=True,
                witness=witness,
            )
            for t in node.targets
            if isinstance(t, SurfaceTargetRef)
        )
        return replace(node, targets=new_targets, witness=witness)
    if isinstance(node, SurfaceDescendantCoordination):
        new_base = replace(
            node.base,
            notes=node.base.notes + ("exception",),
            is_exception=True,
            witness=witness,
        )
        return replace(node, base=new_base, witness=witness)
    return node


def emit_exception_nodes(
    parsed: ParsedException, chapter: str = "", part: str = ""
) -> list[SurfaceNode]:
    """Emit the excepted section nodes, re-stamped with the exception witness.

    Delegates to the section family's emitter for the inner ref, then re-mints
    each node with ``is_exception=True`` / ``"exception"`` note / the exception
    witness (spanning the whole ``lukuun ottamatta … §`` clause), exactly as the
    old ``_lukuun_ottamatta_exception`` does.
    """
    inner = emit_section_nodes(parsed.section, chapter, part)
    witness = SurfaceWitness(
        rule_id="fi.lukuun_ottamatta_exception",
        source_span=(parsed.span.start, parsed.span.end),
    )
    return [_stamp_exception(node, witness) for node in inner]


# ===========================================================================
# Rule: fi.insertion_section_postfix_chapter
#
# Pattern (one or more arms, sep + optional "uusi" between arms):
#     number_list  §(non-GEN)  ( LUKU:ILL number_with_suffix      # § lukuun N
#                              | number_with_suffix LUKU:ILL )     # § N lukuun
# Faithful port of surface_parse._postfix_chapter_section_inserts.  Each arm is a
# (possibly coordinated) section list sharing one postfix chapter; the emitter
# yields one SurfaceInsertion(SECTION) per (section, chapter).
# ===========================================================================


@dataclass(frozen=True, slots=True)
class ParsedPostfixInsert:
    """A recognized postfix-chapter insert: span + (section_label, chapter) pairs."""

    span: Span
    arms: tuple[tuple[str, str], ...]


def _postfix_chapter(scan: _Scan) -> Optional[str]:
    """Parse the postfix chapter after the ``§``: ``lukuun N`` or ``N lukuun``.

    Returns the chapter label (number + optional suffix) or None (rewinding to
    the position before the chapter on failure).
    """
    t = scan.peek()
    # Shape A: § lukuun N  (LUKU:ILL precedes its number).
    if t and t.cat == "LUKU" and t.case == "ILL":
        scan.advance()
        cnum = _number_with_suffix(scan)
        if cnum is not None:
            return cnum[0] + cnum[1]
        return None
    # Shape B: § N lukuun  (number precedes LUKU:ILL).
    if t and t.cat == "NUM":
        saved_b = scan.pos
        cnum = _number_with_suffix(scan)
        t2 = scan.peek()
        if cnum is not None and t2 and t2.cat == "LUKU" and t2.case == "ILL":
            scan.advance()
            return cnum[0] + cnum[1]
        scan.goto(saved_b)
    return None


def _postfix_arm(scan: _Scan) -> Optional[list[tuple[str, str]]]:
    """Parse one ``<num_list> § <postfix_chapter>`` arm.

    Returns ``[(section_label, chapter_label), ...]`` (the coordinated section
    list all sharing one postfix chapter) or None (rewinding ``scan``).
    """
    arm_saved = scan.pos
    nums = _number_list(scan)
    if not nums:
        return None
    t = scan.peek()
    if not (t and t.cat == "PYKALA" and t.case != "GEN"):
        scan.goto(arm_saved)
        return None
    scan.advance()  # consume §
    chap = _postfix_chapter(scan)
    if chap is None:
        scan.goto(arm_saved)
        return None
    labels: list[tuple[str, str]] = []
    for n, sf in nums:
        expanded = _expand_range_single(n)
        for rn in expanded:
            full = rn + (sf if len(expanded) == 1 else "")
            labels.append((full, chap))
    return labels


def recognize_postfix_insert(scan: _Scan, part: str = "") -> Optional[ParsedPostfixInsert]:
    """Recognize ``<num> § lukuun <chap> [, <num> § lukuun <chap>]…``.

    Mirrors ``surface_parse._postfix_chapter_section_inserts``: at least one full
    arm must be present (else None, rewinding the stream, so the generic insert
    path keeps handling section inserts with no postfix chapter).  Arms are joined
    by a separator and an optional repeated ``uusi``.  ``part`` is unused at
    recognition time (it is applied at emit) but kept for signature parity.
    """
    saved = scan.pos
    first = _postfix_arm(scan)
    if first is None:
        scan.goto(saved)
        return None
    arms: list[tuple[str, str]] = list(first)
    while True:
        sep_saved = scan.pos
        if _sep(scan) is None:
            break
        _read(scan, _UUSI)  # optional repeated "uusi" before each arm
        nxt = _postfix_arm(scan)
        if nxt is None:
            scan.goto(sep_saved)
            break
        arms.extend(nxt)
    return ParsedPostfixInsert(span=Span(saved, scan.pos), arms=tuple(arms))


def emit_postfix_insert_nodes(
    parsed: ParsedPostfixInsert, part: str = ""
) -> list[SurfaceNode]:
    """Emit one ``SurfaceInsertion`` (SECTION) per (section, chapter) arm pair.

    All insertions in the group share the one witness spanning the whole postfix
    group, exactly as ``_postfix_chapter_section_inserts`` does.
    """
    witness = SurfaceWitness(
        rule_id="fi.insertion_section_postfix_chapter",
        source_span=(parsed.span.start, parsed.span.end),
    )
    return [
        SurfaceInsertion(
            kind=TargetKind.SECTION,
            label=sec,
            chapter=chap,
            part=part,
            witness=witness,
        )
        for sec, chap in parsed.arms
    ]


__all__ = [
    "NumSuffix",
    "ParsedException",
    "ParsedPostfixInsert",
    "emit_exception_nodes",
    "emit_postfix_insert_nodes",
    "recognize_exception",
    "recognize_postfix_insert",
]
