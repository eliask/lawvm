"""containers — the non-section structural-target recognizer family.

The container family of the combinator-based replacement for
``surface_parse.py``. It recognizes the *non-section* structural targets a
Finnish amendment verb group lists after its verb — chapters, parts, the statute
title (nimike) and appendices (liite):

    [part_ctx] number_list LUKU [otsikko|johd]          (chapter ref)
    LUKU number_with_suffix                             (reversed chapter ref)
    number_list OSA                                     (part ref)
    N osan number_list LUKU ...                         (part-scoped chapter ref)
    [number_list] NIMIKE                                (nimike ref)
    [number_list] LIITE [number_list]                   (appendix ref)

and emits the frozen ``SurfaceTargetRef`` nodes (kind CHAPTER / PART / NIMIKE /
APPENDIX) byte-identically to the old ``_chapter_ref`` / ``_part_ref`` /
``_nimike_ref`` / ``_appendix_ref``.

Bare-section clauses (``N §``) belong to the section family and are DECLINED
here (``recognize_containers`` returns None for them). So does the part-scoped
*section* form (``II osan 1 §`` → a SECTION target): it carries witness ids
outside this family's closed set and is out of scope for the container subset —
declined here so the driver treats the clause as out of scope rather than
miscompiling.

The *renumber* arms of chapter/part refs (``fi.chapter_renumber`` /
``fi.part_renumber``) and the coordinated part+chapter heading shape
(``fi.coordinated_part_chapter_heading_ref``) ARE in scope: a trailing
``numero N:ksi`` produces a CHAPTER_RENUMBER / PART_RENUMBER form, and an
``N osan ja M luvun otsikko`` shape a COORDINATED_HEADING form.

Two enforced layers (per the rewrite contract):

  * LOUD recognizers — pure functions over a ``_Scan`` cursor returning a
    structured intermediate (``ParsedContainer``) carrying spans, the unit kind
    and number/suffix/scope data. No frozen-node construction.
  * a thin emitter (``emit_containers_nodes``) that turns the intermediate into
    the frozen nodes, applying the within-phrase surface structure (range
    expansion, part-scope field carry, heading sub-ref) that IS recognizer/
    emitter work — but NOT cross-verb-group resolution.

Witness ``rule_id``s emitted here (the closed set for this family):
``fi.chapter_ref``, ``fi.chapter_ref_reversed``, ``fi.chapter_renumber``,
``fi.part_ref``, ``fi.part_renumber``,
``fi.coordinated_part_chapter_heading_ref``, ``fi.nimike_ref``,
``fi.appendix_ref``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from lawvm.core.semantic_types import FacetKind
from lawvm.finland.johtolause.grammar.combinators import Span, cat_case
from lawvm.finland.johtolause.grammar.sections import (
    NumSuffix,
    _Scan,
    _expand_range_single,
    _number_list,
    _number_with_suffix,
    _part_ctx,
    _read,
    _renumber_target_list,
    _sep,
)
from lawvm.finland.johtolause.surface_model import (
    SurfaceNode,
    SurfaceSubRef,
    SurfaceTargetRef,
    SurfaceWitness,
    TargetKind,
)

# ---------------------------------------------------------------------------
# Atomic token matchers (the leaf reads that go through the combinator
# substrate; the structural nouns are matched on `.cat` directly inside the
# recognizers, mirroring the old parser's manual cursor).
# ---------------------------------------------------------------------------
_DOC_GEN = cat_case("DOC", "GEN")


# ---------------------------------------------------------------------------
# Recognized container reference (the intermediate the emitter consumes).
# ---------------------------------------------------------------------------


class ContainerForm(Enum):
    """Which container production matched."""

    CHAPTER = "chapter"  # [part_ctx] numlist LUKU [otsikko|johd]
    CHAPTER_REVERSED = "chapter_reversed"  # LUKU number_with_suffix
    PART = "part"  # numlist OSA
    NIMIKE = "nimike"  # [numlist] NIMIKE
    APPENDIX = "appendix"  # [numlist] LIITE [numlist]
    CHAPTER_RENUMBER = "chapter_renumber"  # … numlist LUKU numero N:ksi
    PART_RENUMBER = "part_renumber"  # … numlist OSA numero N:ksi
    COORDINATED_HEADING = "coordinated_heading"  # N osan ja <chapter heading>


@dataclass(frozen=True, slots=True)
class ParsedContainer:
    """A recognized non-section structural target: span + unit + numbers.

    Architecture-neutral: carries only what the recognizer saw (the unit form,
    the numbers/suffixes, an explicit part scope, an optional heading/intro
    facet). The emitter turns this into frozen ``SurfaceTargetRef`` nodes.

    ``renumber_targets`` carries the translative renumber destinations for the
    CHAPTER_RENUMBER / PART_RENUMBER forms (empty otherwise).  ``inner_chapter``
    carries the chapter heading reference of the COORDINATED_HEADING form (the
    chapter half of ``N osan ja M luvun otsikko``), so the emitter can stamp the
    part-heading node with the whole-phrase span while the chapter node keeps its
    own inner span — byte-faithful to the old parser.
    """

    form: ContainerForm
    span: Span
    nums: tuple[NumSuffix, ...] = ()
    explicit_part: Optional[str] = None
    facet: Optional[FacetKind] = None
    renumber_targets: tuple[NumSuffix, ...] = ()
    inner_chapter: Optional["ParsedContainer"] = None


# ---------------------------------------------------------------------------
# Top-level container recognizers (faithful to surface_parse).
# ---------------------------------------------------------------------------


def recognize_chapter_ref(scan: _Scan, part: str = "") -> Optional[ParsedContainer]:
    """Recognize a chapter reference, faithful to ``surface_parse._chapter_ref``.

    Forward form: ``[part_ctx] number_list LUKU [otsikko|johd]``.
    Reversed form: ``LUKU number_with_suffix`` (witness fi.chapter_ref_reversed),
    guarded against a ``LUKU N §`` descendant continuation.
    Renumber arm: ``… number_list LUKU numero N:ksi`` (witness fi.chapter_renumber)
    — a trailing ``numero <renumber_targets>`` turns the chapter ref into a
    CHAPTER_RENUMBER form carrying the translative destinations.
    """
    start = scan.pos
    pt = _part_ctx(scan)
    if pt is None:
        pt = part if part else None
    nums = _number_list(scan)
    reversed_order = False
    if nums:
        t = scan.peek()
        if not (t and t.cat == "LUKU" and t.case != "ILL"):
            scan.goto(start)
            nums = None
    if nums is None:
        scan.goto(start)
        pt = _part_ctx(scan)
        if pt is None:
            pt = part if part else None
        t = scan.peek()
        if not (t and t.cat == "LUKU" and t.case != "ILL"):
            scan.goto(start)
            return None
        scan.advance()
        first = _number_with_suffix(scan)
        if first is None:
            scan.goto(start)
            return None
        # Guard against descendant continuations like "luvun 9 §:ään uusi ...".
        t_after = scan.peek()
        if t_after and t_after.cat == "PYKALA":
            scan.goto(start)
            return None
        nums = [first]
        reversed_order = True
    else:
        scan.advance()  # consume LUKU

    facet: Optional[FacetKind] = None
    if (t := scan.peek()) and t.cat == "OTSIKKO":
        scan.advance()
        facet = FacetKind.HEADING
    elif (t := scan.peek()) and t.cat == "JOHD":
        scan.advance()
        facet = FacetKind.INTRO

    # The "luvun otsikko ja numero N:ksi" CONJ-then-numero quirk: a CONJ bridging
    # the heading facet and the renumber NUMERO is consumed so the renumber arm
    # below sees the NUMERO (faithful to surface_parse._chapter_ref).
    if facet is not None and (t := scan.peek()) and t.cat in {"JA", "CONJ"}:
        nxt = scan.peek(1)
        if nxt and nxt.cat == "NUMERO":
            scan.advance()

    # Renumber arm: "… LUKU numero N:ksi" → CHAPTER_RENUMBER.
    if (t := scan.peek()) and t.cat == "NUMERO":
        scan.advance()  # consume "numero"
        renumber_targets = _renumber_target_list(scan) or []
        return ParsedContainer(
            form=ContainerForm.CHAPTER_RENUMBER,
            span=Span(start, scan.pos),
            nums=tuple(nums),
            explicit_part=pt,
            facet=facet,
            renumber_targets=tuple(renumber_targets),
        )

    form = ContainerForm.CHAPTER_REVERSED if reversed_order else ContainerForm.CHAPTER
    return ParsedContainer(
        form=form,
        span=Span(start, scan.pos),
        nums=tuple(nums),
        explicit_part=pt,
        facet=facet,
    )


def recognize_part_ref(scan: _Scan) -> Optional[ParsedContainer]:
    """Recognize a part reference, faithful to ``surface_parse._part_ref``.

    Whole-part form: ``number_list OSA [otsikko]`` → PART target(s).
    Part-as-context-prefix: ``N osan <chapter_ref>`` → delegate to the chapter
    recognizer (a part-scoped CHAPTER target stays in this family).
    Coordinated heading: ``N osan ja M luvun otsikko`` → COORDINATED_HEADING
    (witness fi.coordinated_part_chapter_heading_ref) — a part heading plus the
    chapter heading, sharing the trailing OTSIKKO.
    Renumber arm: ``number_list OSA numero N:ksi`` → PART_RENUMBER (witness
    fi.part_renumber).

    The part-scoped *section* form (``N osan 1 §``) carries witness ids outside
    this family — declined (rewind) so the clause stays out of scope.
    """
    start = scan.pos
    nums = _number_list(scan)
    if not nums:
        return None
    t = scan.peek()
    if not (t and t.cat == "OSA"):
        scan.goto(start)
        return None
    osa_case = t.case
    scan.advance()

    # Coordinated heading: "N osan ja M luvun otsikko" amends both the part
    # heading and the chapter heading. It must be tried BEFORE the whole-part /
    # context-prefix arms so the leading part does not first become a whole-part
    # target (which would leak the part scope to later explicit targets).
    if osa_case == "GEN" and len(nums) == 1:
        pt = nums[0][0] + nums[0][1]
        saved_coord = scan.pos
        if _sep(scan) is not None:
            ch = recognize_chapter_ref(scan, part=pt)
            if (
                ch is not None
                and ch.form is ContainerForm.CHAPTER
                and ch.facet is FacetKind.HEADING
            ):
                return ParsedContainer(
                    form=ContainerForm.COORDINATED_HEADING,
                    span=Span(start, scan.pos),
                    explicit_part=pt,
                    facet=FacetKind.HEADING,
                    inner_chapter=ch,
                )
        scan.goto(saved_coord)

    # Part as context prefix: "N osan <chapter_ref>". A part-scoped *section*
    # (``N osan 1 §``) is not in this family — the chapter recognizer declines
    # the section shape, and we then fall through and decline the whole clause
    # below (a bare "N osan" with no whole-part facet is itself out of family).
    if osa_case == "GEN" and len(nums) == 1:
        pt = nums[0][0] + nums[0][1]
        saved_prefix = scan.pos
        ch = recognize_chapter_ref(scan, part=pt)
        if ch is not None:
            return ch
        scan.goto(saved_prefix)

    # Whole-part target: "II osa" or "II osan otsikko".
    facet: Optional[FacetKind] = None
    if (t := scan.peek()) and t.cat == "OTSIKKO":
        scan.advance()
        facet = FacetKind.HEADING

    # Renumber arm: "number_list OSA numero N:ksi" → PART_RENUMBER.
    if (t := scan.peek()) and t.cat == "NUMERO":
        scan.advance()  # consume "numero"
        renumber_targets = _renumber_target_list(scan) or []
        return ParsedContainer(
            form=ContainerForm.PART_RENUMBER,
            span=Span(start, scan.pos),
            nums=tuple(nums),
            facet=facet,
            renumber_targets=tuple(renumber_targets),
        )

    return ParsedContainer(
        form=ContainerForm.PART,
        span=Span(start, scan.pos),
        nums=tuple(nums),
        facet=facet,
    )


def recognize_nimike_ref(scan: _Scan) -> Optional[ParsedContainer]:
    """Recognize a nimike (statute title) reference, faithful to ``_nimike_ref``.

    ``[number_list] [DOC:GEN] NIMIKE`` → a single NIMIKE target, label "".
    """
    start = scan.pos
    _number_list(scan)  # optional leading number (rare), value unused
    if _read(scan, _DOC_GEN) is not None:
        pass  # skip optional doc-type genitive
    t = scan.peek()
    if not (t and t.cat == "NIMIKE"):
        scan.goto(start)
        return None
    scan.advance()
    return ParsedContainer(form=ContainerForm.NIMIKE, span=Span(start, scan.pos))


def recognize_appendix_ref(scan: _Scan) -> Optional[ParsedContainer]:
    """Recognize an appendix (liite) reference, faithful to ``_appendix_ref``.

    ``[number_list] LIITE [number_list]`` → APPENDIX target(s). A leading number
    list takes precedence; otherwise a trailing one is consumed.
    """
    start = scan.pos
    pre_nums = _number_list(scan)
    t = scan.peek()
    if not (t and t.cat == "LIITE"):
        scan.goto(start)
        return None
    scan.advance()
    post_nums = _number_list(scan) if not pre_nums else None
    nums = pre_nums or post_nums or []
    return ParsedContainer(
        form=ContainerForm.APPENDIX,
        span=Span(start, scan.pos),
        nums=tuple(nums),
    )


def recognize_containers(
    scan: _Scan, chapter: str = "", part: str = ""
) -> Optional[ParsedContainer]:
    """Recognize one non-section container target at the cursor.

    Tries chapter → part → nimike → appendix, mirroring the old ``_target``
    dispatch order for the container families (section comes first there, but a
    section ref is the section family's job: by the time the container family is
    consulted the cursor is known not to start a section ref). Returns None
    (rewinding) when no container reference matches, so the driver can decline.

    ``chapter`` is accepted for signature symmetry with the section family but is
    unused (chapters never inherit a chapter scope); ``part`` is the inherited
    part scope applied to a forward chapter ref where no explicit part parsed.
    """
    del chapter  # chapters do not inherit a chapter scope
    start = scan.pos
    parsed = recognize_chapter_ref(scan, part=part)
    if parsed is not None:
        return parsed
    scan.goto(start)
    parsed = recognize_part_ref(scan)
    if parsed is not None:
        return parsed
    scan.goto(start)
    parsed = recognize_nimike_ref(scan)
    if parsed is not None:
        return parsed
    scan.goto(start)
    parsed = recognize_appendix_ref(scan)
    if parsed is not None:
        return parsed
    scan.goto(start)
    return None


# ---------------------------------------------------------------------------
# Emitter — ParsedContainer -> frozen Surface* nodes (faithful to the old
# parser's node construction).
# ---------------------------------------------------------------------------


def _facet_sub_refs(facet: Optional[FacetKind]) -> tuple[SurfaceSubRef, ...]:
    return (SurfaceSubRef(facet=facet),) if facet else ()


def _emit_chapter(parsed: ParsedContainer) -> list[SurfaceNode]:
    """Emit chapter targets (faithful to the body of ``_chapter_ref``)."""
    rid = (
        "fi.chapter_ref_reversed"
        if parsed.form is ContainerForm.CHAPTER_REVERSED
        else "fi.chapter_ref"
    )
    w = SurfaceWitness(rule_id=rid, source_span=(parsed.span.start, parsed.span.end))
    pt = parsed.explicit_part or ""
    sub_refs = _facet_sub_refs(parsed.facet)
    nodes: list[SurfaceNode] = []
    for n, sf in parsed.nums:
        for rn in _expand_range_single(n):
            nodes.append(
                SurfaceTargetRef(
                    kind=TargetKind.CHAPTER,
                    label=rn + sf,
                    chapter="",
                    part=pt,
                    sub_refs=sub_refs,
                    witness=w,
                )
            )
    return nodes


def _emit_part(parsed: ParsedContainer) -> list[SurfaceNode]:
    """Emit whole-part targets (faithful to the body of ``_part_ref``)."""
    w = SurfaceWitness(rule_id="fi.part_ref", source_span=(parsed.span.start, parsed.span.end))
    sub_refs = _facet_sub_refs(parsed.facet)
    nodes: list[SurfaceNode] = []
    for n, sf in parsed.nums:
        nodes.append(
            SurfaceTargetRef(
                kind=TargetKind.PART,
                label=n + sf,
                chapter="",
                part="",
                sub_refs=sub_refs,
                witness=w,
            )
        )
    return nodes


def _renumber_destinations(parsed: ParsedContainer) -> dict[str, str]:
    """Map each source label to its translative renumber destination.

    Faithful to surface_parse._chapter_ref / _part_ref renumber bookkeeping: the
    source labels are the (already range-expanded) numbers, the destination
    labels are the renumber targets with any ``:ksi`` translative marker stripped.
    A length mismatch yields no mapping (the old parser's ``zip(strict=True)``
    guard), so nodes carry an empty destination.
    """
    source_labels = [n + sf for n, sf in parsed.nums]
    destination_labels = [(n + sf).removesuffix(":ksi") for n, sf in parsed.renumber_targets]
    if len(source_labels) != len(destination_labels):
        return {}
    return dict(zip(source_labels, destination_labels, strict=True))


def _emit_chapter_renumber(parsed: ParsedContainer) -> list[SurfaceNode]:
    """Emit chapter-renumber targets (faithful to _chapter_ref's NUMERO branch)."""
    w = SurfaceWitness(
        rule_id="fi.chapter_renumber", source_span=(parsed.span.start, parsed.span.end)
    )
    pt = parsed.explicit_part or ""
    sub_refs = _facet_sub_refs(parsed.facet)
    dest_by_source = _renumber_destinations(parsed)
    nodes: list[SurfaceNode] = []
    for n, sf in parsed.nums:
        for rn in _expand_range_single(n):
            dest_label = dest_by_source.get(rn + sf, "")
            notes_list = ["renumber_clause"]
            if dest_label:
                notes_list.append(f"renumber_destination={dest_label}")
            nodes.append(
                SurfaceTargetRef(
                    kind=TargetKind.CHAPTER,
                    label=rn + sf,
                    chapter="",
                    part=pt,
                    sub_refs=sub_refs,
                    notes=tuple(notes_list),
                    renumber_dest=dest_label,
                    witness=w,
                )
            )
    return nodes


def _emit_part_renumber(parsed: ParsedContainer) -> list[SurfaceNode]:
    """Emit part-renumber targets (faithful to _part_ref's NUMERO branch)."""
    w = SurfaceWitness(
        rule_id="fi.part_renumber", source_span=(parsed.span.start, parsed.span.end)
    )
    sub_refs = _facet_sub_refs(parsed.facet)
    dest_by_source = _renumber_destinations(parsed)
    nodes: list[SurfaceNode] = []
    for n, sf in parsed.nums:
        dest_label = dest_by_source.get(n + sf, "")
        notes_list = ["renumber_clause"]
        if dest_label:
            notes_list.append(f"renumber_destination={dest_label}")
        nodes.append(
            SurfaceTargetRef(
                kind=TargetKind.PART,
                label=n + sf,
                chapter="",
                part="",
                sub_refs=sub_refs,
                notes=tuple(notes_list),
                renumber_dest=dest_label,
                witness=w,
            )
        )
    return nodes


def _emit_coordinated_heading(parsed: ParsedContainer) -> list[SurfaceNode]:
    """Emit the coordinated part+chapter heading shape (faithful to _part_ref).

    A part-heading target (witness fi.coordinated_part_chapter_heading_ref, span
    the whole coordinated phrase) followed by the chapter heading node(s), which
    keep their own inner chapter span and fi.chapter_ref witness.
    """
    assert parsed.inner_chapter is not None  # set by recognize_part_ref
    part_heading = SurfaceTargetRef(
        kind=TargetKind.PART,
        label=parsed.explicit_part or "",
        sub_refs=(SurfaceSubRef(facet=FacetKind.HEADING),),
        witness=SurfaceWitness(
            rule_id="fi.coordinated_part_chapter_heading_ref",
            source_span=(parsed.span.start, parsed.span.end),
        ),
    )
    return [part_heading, *_emit_chapter(parsed.inner_chapter)]


def _emit_nimike(parsed: ParsedContainer) -> list[SurfaceNode]:
    w = SurfaceWitness(rule_id="fi.nimike_ref", source_span=(parsed.span.start, parsed.span.end))
    return [SurfaceTargetRef(kind=TargetKind.NIMIKE, label="", witness=w)]


def _emit_appendix(parsed: ParsedContainer) -> list[SurfaceNode]:
    w = SurfaceWitness(rule_id="fi.appendix_ref", source_span=(parsed.span.start, parsed.span.end))
    if parsed.nums:
        return [
            SurfaceTargetRef(kind=TargetKind.APPENDIX, label=n + sf, witness=w)
            for n, sf in parsed.nums
        ]
    return [SurfaceTargetRef(kind=TargetKind.APPENDIX, label="", witness=w)]


def emit_containers_nodes(
    parsed: ParsedContainer, chapter: str = "", part: str = ""
) -> list[SurfaceNode]:
    """Turn a recognized ``ParsedContainer`` into frozen Surface* nodes.

    ``chapter`` / ``part`` are accepted for signature symmetry with the section
    family; the explicit scope is already captured in ``parsed`` (the old
    container recognizers apply the inherited ``part`` during recognition, not
    emission), so these parameters are unused here.
    """
    del chapter, part
    if parsed.form in (ContainerForm.CHAPTER, ContainerForm.CHAPTER_REVERSED):
        return _emit_chapter(parsed)
    if parsed.form is ContainerForm.CHAPTER_RENUMBER:
        return _emit_chapter_renumber(parsed)
    if parsed.form is ContainerForm.PART:
        return _emit_part(parsed)
    if parsed.form is ContainerForm.PART_RENUMBER:
        return _emit_part_renumber(parsed)
    if parsed.form is ContainerForm.COORDINATED_HEADING:
        return _emit_coordinated_heading(parsed)
    if parsed.form is ContainerForm.NIMIKE:
        return _emit_nimike(parsed)
    return _emit_appendix(parsed)
