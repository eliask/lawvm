"""headings — the heading recognizer family (väliotsikko placement + backref).

The recognizer family for the heading shapes a Finnish amendment verb group
lists: a heading placed before a section, and an unresolved heading backref.

It recognizes three context-free shapes and emits the frozen ``Surface*`` nodes
(``SurfaceHeadingPlacement`` / ``SurfaceValiotsikkoRef``) byte-identically to the
old ``surface_parse`` heading paths:

  * ``uusi väliotsikko N §:n edelle``   — heading placement, ``uusi`` first.
    Recognizer entry is the ``OTSIKKO`` (after the driver consumed ``uusi``);
    one ``SurfaceHeadingPlacement`` for the single target section.
    (old ``_heading_placement_after_uusi``; ``fi.heading_edelle_otsikko_after_uusi``)
  * ``<num_list> §:n edelle [uusi] väliotsikko`` — heading placement, target
    first; one ``SurfaceHeadingPlacement`` per coordinated/range target section.
    (old ``_trailing_heading_placement_arm``; ``fi.heading_edelle_otsikko_target_list``)
  * a lone ``VALIOTSIKKO`` sentinel (the ``sen/pykälän/niiden edellä oleva
    väliotsikko`` phrase the scanner collapses into one token) — an unresolved
    heading backref ``SurfaceValiotsikkoRef``.
    (old VALIOTSIKKO branch; ``fi.valiotsikko_heading_ref``)

Two enforced layers (per the rewrite contract):

  * LOUD recognizers — pure functions over a ``_Scan`` cursor that return a
    structured intermediate (``ParsedHeading``) carrying the span + raw target
    numbers.  Built on the same scanner substrate as the section family; no
    frozen-node construction.
  * a thin emitter (``emit_headings_nodes``) turning the intermediate into the
    frozen nodes.  Range/list expansion of the target sections IS recognizer/
    emitter surface structure; cross-verb-group resolution is NOT done here.

The ``VALIOTSIKKO`` sentinel reaches this family already collapsed by
``scan.apply_annotations_with_jolloin_pairs``; the heading-CHANGE form
``N §:n edellä oleva väliotsikko`` (no ``sen/pykälän`` anaphor) is NOT collapsed
and stays a section-family ``fi.section_ref`` HEADING-facet target — it is out of
scope here.

Heading insertions that introduce a new chapter heading (``uusi N luvun otsikko``)
emit ``SurfaceTargetRef`` / ``SurfaceInsertion`` and belong to the insertion
family (``fi.insertion_*``); they are NOT recognized here.

Two further heading shapes are recognized as single-owner continuation arms:

  * ``<N> §:n edelle uusi [<M>] luvun otsikko`` — a heading placed before a
    single section ``N`` whose payload is a CHAPTER heading (``luvun otsikko``).
    The old parser handles this with an 8-token look-ahead window inside the
    ``_target_list`` continuation loop; it emits one ``SurfaceHeadingPlacement``
    for section ``N`` only when the arm began with a ``NUM``.  The recognizer
    reproduces exactly that NUM-led emitting case (span from the entry NUM
    through the matched ``otsikko``); the non-emitting window variants (the arm
    began with ``EDELLA`` / ``WORD``, no placement node minted) and the optional
    follow-on ``[, ] <target>`` continuation belong to the driver and are NOT
    the recognizer's job.  (old window arm; ``fi.heading_edelle_luvun_otsikko``)
  * ``mukaanluettuna <num_list> §:n edellä olevan väliotsikon`` — an explicit
    preceding-heading facet attached to a section range, emitting one
    ``SurfaceTargetRef`` (SECTION kind, HEADING facet) per expanded section.
    (old ``_consume_including_preceding_heading_target``;
    ``fi.including_preceding_heading_target``)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from lawvm.core.semantic_types import FacetKind
from lawvm.finland.johtolause.grammar.combinators import Span, cat
from lawvm.finland.johtolause.grammar.sections import (
    NumSuffix,
    _Scan,
    _number_list,
    _read,
)
from lawvm.finland.johtolause.surface_model import (
    SurfaceHeadingPlacement,
    SurfaceNode,
    SurfaceSubRef,
    SurfaceTargetRef,
    SurfaceValiotsikkoRef,
    SurfaceWitness,
    TargetKind,
)

# Atomic token matchers reused by the heading recognizers.
_UUSI = cat("UUSI")
_OTSIKKO = cat("OTSIKKO")
_VALIOTSIKKO = cat("VALIOTSIKKO")


# ---------------------------------------------------------------------------
# Recognized heading intermediate (the emitter consumes this).
# ---------------------------------------------------------------------------


class HeadingForm(Enum):
    """Which heading production matched."""

    AFTER_UUSI = "after_uusi"  # uusi väliotsikko N §:n edelle (single target)
    TARGET_LIST = "target_list"  # <num_list> §:n edelle [uusi] väliotsikko
    VALIOTSIKKO_REF = "valiotsikko_ref"  # lone VALIOTSIKKO sentinel (backref)
    LUVUN_OTSIKKO = "luvun_otsikko"  # <N> §:n edelle uusi [M] luvun otsikko
    INCLUDING_PRECEDING = "including_preceding"  # mukaanluettuna N §:n edellä oleva otsikko


# The witness rule_id per form (the closed set for this family).
_FORM_RULE_ID: dict[HeadingForm, str] = {
    HeadingForm.AFTER_UUSI: "fi.heading_edelle_otsikko_after_uusi",
    HeadingForm.TARGET_LIST: "fi.heading_edelle_otsikko_target_list",
    HeadingForm.VALIOTSIKKO_REF: "fi.valiotsikko_heading_ref",
    HeadingForm.LUVUN_OTSIKKO: "fi.heading_edelle_luvun_otsikko",
    HeadingForm.INCLUDING_PRECEDING: "fi.including_preceding_heading_target",
}


@dataclass(frozen=True, slots=True)
class ParsedHeading:
    """A recognized heading reference: form + span + raw target numbers.

    Architecture-neutral: carries only what the recognizer saw (the matched
    span and, for the placement forms, the target-section number/suffix list).
    The emitter turns this into frozen ``Surface*`` nodes, applying range/list
    expansion of the target sections.
    """

    form: HeadingForm
    span: Span
    nums: tuple[NumSuffix, ...] = ()
    chapter: str = ""
    part: str = ""


def heading_rule_id(parsed: ParsedHeading) -> str:
    """The witness ``rule_id`` the emitter attaches for a recognized heading."""
    return _FORM_RULE_ID[parsed.form]


# ---------------------------------------------------------------------------
# Recognizers (pure functions over the cursor; None rewinds).
# ---------------------------------------------------------------------------


def recognize_valiotsikko_ref(
    scan: _Scan, chapter: str = "", part: str = ""
) -> Optional[ParsedHeading]:
    """Recognize a lone ``VALIOTSIKKO`` sentinel — a heading backref.

    Mirrors the old VALIOTSIKKO branch: a single sentinel token, span over just
    that token.  The driver owns any preceding separator (and its inclusion in
    the witness span), exactly as the old parser's loop did — see the validation
    driver, which records ``saved`` before the separator and rewrites the span.
    """
    start = scan.pos
    if _read(scan, _VALIOTSIKKO) is None:
        return None
    return ParsedHeading(form=HeadingForm.VALIOTSIKKO_REF, span=Span(start, scan.pos))


def recognize_heading_after_uusi(
    scan: _Scan, chapter: str = "", part: str = ""
) -> Optional[ParsedHeading]:
    """Recognize ``väliotsikko N §:n edelle`` (``uusi`` already consumed).

    Mirrors ``surface_parse._heading_placement_after_uusi``: the recognizer
    entry is the ``OTSIKKO`` token (the caller/driver has consumed the leading
    ``uusi``), then a single-target number list, ``§:GEN``, ``edelle``.  The span
    starts at the ``OTSIKKO`` (the ``uusi`` is NOT included), matching the old
    witness ``(saved, s.pos)`` taken at function entry.
    """
    start = scan.pos
    if _read(scan, _OTSIKKO) is None:
        scan.goto(start)
        return None

    nums = _number_list(scan)
    if not nums or len(nums) != 1:
        scan.goto(start)
        return None

    t = scan.peek()
    if not (t and t.cat == "PYKALA" and t.case == "GEN"):
        scan.goto(start)
        return None
    scan.advance()

    t = scan.peek()
    if not (t and t.cat == "EDELLA"):
        scan.goto(start)
        return None
    scan.advance()

    return ParsedHeading(
        form=HeadingForm.AFTER_UUSI,
        span=Span(start, scan.pos),
        nums=tuple(nums),
        chapter=chapter,
        part=part,
    )


def recognize_trailing_heading_placement(
    scan: _Scan, chapter: str = "", part: str = ""
) -> Optional[ParsedHeading]:
    """Recognize ``<num_list> §:n edelle [uusi] väliotsikko`` (target-first).

    Mirrors ``surface_parse._trailing_heading_placement_arm``: a (possibly
    coordinated / em-dash range) section number list, ``§:GEN``, ``edelle``, an
    optional ``uusi``, then ``OTSIKKO``.  One ``SurfaceHeadingPlacement`` per
    expanded target section.  Span runs from the number list through ``OTSIKKO``.
    """
    start = scan.pos

    nums = _number_list(scan)
    if not nums:
        scan.goto(start)
        return None

    t = scan.peek()
    if not (t and t.cat == "PYKALA" and t.case == "GEN"):
        scan.goto(start)
        return None
    scan.advance()

    t = scan.peek()
    if not (t and t.cat == "EDELLA"):
        scan.goto(start)
        return None
    scan.advance()

    # Optional ``uusi`` (the caller may already have consumed it).
    _read(scan, _UUSI)

    t = scan.peek()
    if not (t and t.cat == "OTSIKKO"):
        scan.goto(start)
        return None
    scan.advance()

    return ParsedHeading(
        form=HeadingForm.TARGET_LIST,
        span=Span(start, scan.pos),
        nums=tuple(nums),
        chapter=chapter,
        part=part,
    )


def recognize_heading_edelle_luvun_otsikko(
    scan: _Scan, chapter: str = "", part: str = ""
) -> Optional[ParsedHeading]:
    """Recognize the NUM-led ``<N> §:n edelle uusi [<M>] luvun otsikko`` arm.

    Faithful port of the emitting case of ``surface_parse._target_list``'s heading
    look-ahead window (the ``fi.heading_edelle_luvun_otsikko`` branch).  The arm
    enters on a ``NUM`` (the anchor section ``N``); the window scans forward up to
    8 tokens for one of:

      * ``uusi otsikko``
      * ``uusi <LUKU:GEN> otsikko``
      * ``uusi <NUM> [<LETTER>] <LUKU:GEN> otsikko``

    breaking early on a ``VERB``, a second ``NUM`` (only one allowed before the
    payload), or a non-GEN ``PYKALA``.  On a match it advances the cursor to just
    past the matched ``otsikko`` and emits ONE ``SurfaceHeadingPlacement`` for
    section ``N`` (label = entry NUM + an optional immediately-following LETTER
    suffix), span = entry → match-end.

    The non-NUM-led window variants (the old branch also fires when the arm began
    with ``EDELLA`` / ``WORD`` but mints NO placement node) and the optional
    follow-on ``[, ] <target>`` continuation are driver-loop behaviour, not the
    recognizer's, and are intentionally out of scope here.
    """
    start = scan.pos
    t = scan.peek()
    if t is None or t.cat != "NUM":
        return None

    toks = scan.cur.tokens
    n_toks = len(toks)
    hk = start
    allow_num = True  # the entry NUM itself
    found_end: Optional[int] = None
    while hk < n_toks and hk < start + 8:
        tk = toks[hk]
        if tk.cat == "UUSI":
            nxt1 = toks[hk + 1] if hk + 1 < n_toks else None
            nxt2 = toks[hk + 2] if hk + 2 < n_toks else None
            if nxt1 and nxt1.cat == "OTSIKKO":
                found_end = hk + 2
                break
            if (
                nxt1
                and nxt1.cat == "LUKU"
                and nxt1.case == "GEN"
                and nxt2
                and nxt2.cat == "OTSIKKO"
            ):
                found_end = hk + 3
                break
            if nxt1 and nxt1.cat == "NUM":
                sfx = 2
                if nxt2 and nxt2.cat == "LETTER":
                    sfx = 3
                luku_t = toks[hk + sfx] if hk + sfx < n_toks else None
                otsikko_t = toks[hk + sfx + 1] if hk + sfx + 1 < n_toks else None
                if (
                    luku_t
                    and luku_t.cat == "LUKU"
                    and luku_t.case == "GEN"
                    and otsikko_t
                    and otsikko_t.cat == "OTSIKKO"
                ):
                    found_end = hk + sfx + 2
                    break
        if tk.cat == "VERB":
            break
        if tk.cat == "NUM" and not allow_num:
            break
        if tk.cat == "PYKALA" and tk.case != "GEN":
            break
        if allow_num and tk.cat == "NUM":
            allow_num = False
        hk += 1

    if found_end is None:
        scan.goto(start)
        return None

    # Label = entry NUM + an immediately-following LETTER suffix (the old branch
    # reads ``_pre_t`` (= entry) and ``_pre_t + 1``).
    sec_num = t.text
    sfx_tok = toks[start + 1] if start + 1 < n_toks else None
    sec_sfx = sfx_tok.lemma if (sfx_tok is not None and sfx_tok.cat == "LETTER") else ""

    scan.goto(found_end)
    return ParsedHeading(
        form=HeadingForm.LUVUN_OTSIKKO,
        span=Span(start, found_end),
        nums=((sec_num, sec_sfx),),
        chapter=chapter,
        part=part,
    )


def recognize_including_preceding_heading_target(
    scan: _Scan, chapter: str = "", part: str = ""
) -> Optional[ParsedHeading]:
    """Recognize ``mukaanluettuna <num_list> §:n edellä olevan väliotsikon``.

    Faithful port of ``surface_parse._consume_including_preceding_heading_target``:
    the literal word ``mukaanluettuna``, a section number list, ``§:GEN``,
    ``edellä``, the verb ``olla`` (any inflection — matched by lemma), then
    ``OTSIKKO``.  Emits one ``SurfaceTargetRef`` (SECTION kind, HEADING facet)
    per expanded section; span from the leading word through ``OTSIKKO``.
    """
    start = scan.pos
    t = scan.peek()
    if not (t and t.cat == "WORD" and t.text.lower() == "mukaanluettuna"):
        return None
    scan.advance()

    nums = _number_list(scan)
    if not nums:
        scan.goto(start)
        return None

    t = scan.peek()
    if not (t and t.cat == "PYKALA" and t.case == "GEN"):
        scan.goto(start)
        return None
    scan.advance()

    t = scan.peek()
    if not (t and t.cat == "EDELLA"):
        scan.goto(start)
        return None
    scan.advance()

    t = scan.peek()
    if not (t and (t.lemma or "").lower() == "olla"):
        scan.goto(start)
        return None
    scan.advance()

    t = scan.peek()
    if not (t and t.cat == "OTSIKKO"):
        scan.goto(start)
        return None
    scan.advance()

    return ParsedHeading(
        form=HeadingForm.INCLUDING_PRECEDING,
        span=Span(start, scan.pos),
        nums=tuple(nums),
        chapter=chapter,
        part=part,
    )


# ---------------------------------------------------------------------------
# Emitter — ParsedHeading -> frozen Surface* nodes.
# ---------------------------------------------------------------------------


def _expanded_target_labels(nums: tuple[NumSuffix, ...]) -> list[str]:
    """Expand a target number list to section labels (faithful label rule).

    Mirrors the old ``_trailing_heading_placement_arm`` comprehension, which
    emits one heading per ``(n, sf)`` raw number-suffix pair — the number list
    has already expanded em-dash ranges into individual pairs, so no further
    per-pair range expansion is applied to the label here.
    """
    return [n + sf for n, sf in nums]


def emit_headings_nodes(
    parsed: ParsedHeading, chapter: str = "", part: str = ""
) -> list[SurfaceNode]:
    """Turn a recognized ``ParsedHeading`` into frozen ``Surface*`` nodes.

    ``chapter`` / ``part`` are the inherited context the driver supplies; the
    recognizer also captures any context passed at recognition time on
    ``parsed`` (the two agree in practice — the driver threads the same values).
    The emitter prefers the values on ``parsed`` to stay faithful to the old
    parser, which baked the context into the node at construction.
    """
    rule_id = heading_rule_id(parsed)
    span = (parsed.span.start, parsed.span.end)
    ch = parsed.chapter or chapter
    pt = parsed.part or part

    if parsed.form is HeadingForm.VALIOTSIKKO_REF:
        return [SurfaceValiotsikkoRef(witness=SurfaceWitness(rule_id=rule_id, source_span=span))]

    w = SurfaceWitness(rule_id=rule_id, source_span=span)

    if parsed.form is HeadingForm.INCLUDING_PRECEDING:
        # One section-kind target per expanded section, carrying the HEADING
        # facet (faithful to ``_consume_including_preceding_heading_target``).
        return [
            SurfaceTargetRef(
                kind=TargetKind.SECTION,
                label=label,
                chapter=ch,
                part=pt,
                sub_refs=(SurfaceSubRef(facet=FacetKind.HEADING),),
                witness=w,
            )
            for label in _expanded_target_labels(parsed.nums)
        ]

    # AFTER_UUSI / TARGET_LIST / LUVUN_OTSIKKO — heading-placement nodes.
    return [
        SurfaceHeadingPlacement(
            target_section=label,
            chapter=ch,
            part=pt,
            witness=w,
        )
        for label in _expanded_target_labels(parsed.nums)
    ]


__all__ = [
    "HeadingForm",
    "ParsedHeading",
    "emit_headings_nodes",
    "heading_rule_id",
    "recognize_heading_after_uusi",
    "recognize_heading_edelle_luvun_otsikko",
    "recognize_including_preceding_heading_target",
    "recognize_trailing_heading_placement",
    "recognize_valiotsikko_ref",
]
