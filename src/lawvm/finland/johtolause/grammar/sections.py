"""sections — the section-reference recognizer family (rewrite slice 1).

The first real recognizer family of the combinator-based replacement for
``surface_parse.py``. It recognizes the section-reference shapes a Finnish
amendment verb group lists after its verb:

    [PART_ctx] [CHAPTER_ctx] number_list § [sub_ref ...]
    number_list § numero renumber_targets        (renumber arm)
    pykälien number_list                          (genitive-plural prefix form)

and emits the frozen ``Surface*`` nodes (``SurfaceTargetRef`` /
``SurfaceScopeBlock`` / ``SurfaceDescendantCoordination``) byte-identically to
the old ``_section_ref`` / ``_pykala_prefix_section_ref``.

Two enforced layers (per the rewrite contract):

  * LOUD recognizers — pure functions over a ``Cursor`` that return a structured
    intermediate (``ParsedSection``) carrying spans and number/suffix/sub-ref
    data. Built from the ``combinators`` substrate; no frozen-node construction.
  * a thin emitter (``emit_section_nodes``) that turns the intermediate into the
    frozen nodes, applying the within-phrase surface structure (scope-block
    wrapping, sub-ref coordination, facet distribution, range expansion) that IS
    recognizer/emitter work — but NOT cross-verb-group resolution.

Witness ``rule_id``s emitted here (the closed set for this slice):
``fi.section_ref``, ``fi.section_ref_pykala_prefix``, ``fi.scope_block_chapter``,
``fi.scope_block_part``, ``fi.section_renumber``.

Out of scope for slice 1 (the driver raises ``OutOfScope`` for these): insertions
(``uusi``), headings, backrefs, meta/text-amend, move tails, jolloin, exception
clauses, cross-verb-group chapter/part inheritance, and anaphora.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from lawvm.core.semantic_types import FacetKind
from lawvm.finland.johtolause.grammar.combinators import (
    Cursor,
    Span,
    cat,
    cat_case,
)
from lawvm.finland.johtolause.lexicon import Token
from lawvm.finland.johtolause.sentinels import SKIP_CATS as _SENTINEL_CATS
from lawvm.finland.johtolause.surface_model import (
    ScopeKind,
    SurfaceDescendantCoordination,
    SurfaceNode,
    SurfaceScopeBlock,
    SurfaceSubRef,
    SurfaceTargetRef,
    SurfaceWitness,
    TargetKind,
)

# The sub-reference recognizer cluster (``SubRef``, ``_sub_ref``,
# ``_parse_descendant_coordination``, ``_named_descriptor_subref`` and helpers)
# lives in ``grammar.subref``. It is re-imported at the BOTTOM of this module —
# after the low-level scanner / number-list helpers it depends on are defined —
# so external callers (``S.SubRef`` / ``S._sub_ref`` / …) keep resolving and the
# import cycle is broken by ordering. See the re-import block near end of file.


# A (number, letter-suffix) pair, e.g. ("5", "a") or ("12", "").
NumSuffix = tuple[str, str]


# ---------------------------------------------------------------------------
# Recognized section reference (the intermediate the emitter consumes).
# ---------------------------------------------------------------------------


class SectionForm(Enum):
    """Which section-reference production matched."""

    SUFFIX = "suffix"  # number_list § [sub_ref] (the workhorse form)
    RENUMBER = "renumber"  # number_list § numero renumber_targets
    PYKALA_PREFIX = "pykala_prefix"  # pykälien number_list


@dataclass(frozen=True, slots=True)
class ParsedSection:
    """A recognized section reference: spans + numbers + sub-refs.

    Architecture-neutral: carries only what the recognizer saw (numbers,
    suffixes, sub-refs, explicit scope), plus the span. The emitter turns this
    into frozen Surface* nodes.
    """

    form: SectionForm
    span: Span
    nums: tuple[NumSuffix, ...]
    explicit_chapter: Optional[str] = None
    explicit_part: Optional[str] = None
    subs: tuple[SubRef, ...] = ()
    renumber_targets: tuple[NumSuffix, ...] = ()
    # A renumber arm may carry a trailing ``(ja|,) mainitun pykälän …`` anaphoric
    # back-reference (``§:n numero N:ksi ja mainitun pykälän 1 momentti``). The
    # old ``_section_ref`` consumes it inline and re-emits the renumber source
    # sections a second time, scoped to these sub-refs, with the extra
    # ``renumber_backref_clause`` note. Empty when no such continuation was read.
    renumber_backref_subs: tuple[SubRef, ...] = ()
    # The renumber span END *before* any back-reference tail was consumed. The
    # old parser emits the base renumber nodes with the pre-backref witness span
    # and the back-reference nodes with the full (post-backref) span; this records
    # the split point so the emitter reproduces both byte-identically. ``None``
    # when there is no back-reference tail (base span == ``span``).
    renumber_base_span_end: Optional[int] = None
    # For a 2-number suffix list: whether the last separator before § is a
    # conjunction (a "parallel pair", both numbers scoped) rather than a comma
    # (leading number is whole-section). Computed at recognition time from the
    # raw tokens, exactly as the old parser's backward scan does.
    last_sep_is_conj: bool = False


# ---------------------------------------------------------------------------
# Number / range helpers (faithful to surface_parse).
# ---------------------------------------------------------------------------


def _expand_range_single(n: str) -> list[str]:
    """If ``n`` looks like '21–23', expand to ['21','22','23']; else [n]."""
    from lawvm.finland.johtolause.lexicon import _RANGE_RE

    m = _RANGE_RE.match(n)
    if m:
        start, end = int(m.group(1)), int(m.group(2))
        if end > start and end - start < 50:
            return [str(i) for i in range(start, end + 1)]
    return [n]


def _expand_range(start: NumSuffix, end: NumSuffix) -> list[NumSuffix]:
    """Expand a range pair into individual (number, suffix) tuples."""
    sn, ss = start
    en, es = end
    # Same base + letter range: 11a–11d
    if sn == en and ss and es:
        a, b = ord(ss), ord(es)
        if a <= b and b - a < 26:
            return [(sn, chr(c)) for c in range(a, b + 1)]
    # Numeric range: 21–23
    if sn.isdigit() and en.isdigit():
        si, ei = int(sn), int(en)
        if ss and not es and si < ei and ei - si < 50:
            return [(sn, ss)] + [(str(i), "") for i in range(si + 1, ei + 1)]
        if si <= ei and ei - si < 50:
            return [(str(i), ss) for i in range(si, ei + 1)]
    return [start]


# ---------------------------------------------------------------------------
# A tiny mutable scanner over the Cursor, mirroring the old Stream's idioms.
#
# The combinators give transactional primitives, but the section family is a
# sequence of context-sensitive choices that the old parser expressed as a
# manual cursor. We keep that shape (it is the behavioral ground truth) by
# wrapping the immutable Cursor in a thin advancing scanner — every recognizer
# is still a pure function returning a new position, so backtracking remains
# "drop the new position, keep the old".
# ---------------------------------------------------------------------------


@dataclass
class _Scan:
    """A mutable position over the token stream (recognizer-local scratch)."""

    cur: Cursor

    @property
    def pos(self) -> int:
        return self.cur.pos

    def peek(self, offset: int = 0) -> Optional[Token]:
        return self.cur.peek(offset)

    def advance(self, n: int = 1) -> None:
        self.cur = self.cur.advance(n)

    def goto(self, pos: int) -> None:
        self.cur = Cursor(self.cur.tokens, pos)


# Combinator-built atomic token matchers (used by the scanner's leaf reads).
_NUM = cat("NUM")
_LETTER = cat("LETTER")
_PYKALA = cat("PYKALA")
_COMMA = cat("COMMA")
_CONJ = cat("CONJ")
_DASH = cat("DASH")
_NUMERO = cat("NUMERO")
_BACKREF = cat("BACKREF")
_LUKU_GEN = cat_case("LUKU", "GEN")
_OSA_GEN = cat_case("OSA", "GEN")


def _read(scan: _Scan, parser) -> Optional[Token]:
    """Run a single-token combinator at the scanner's position; advance on hit."""
    r = parser(scan.cur)
    if r.ok:
        assert r.next is not None
        scan.cur = r.next
        return r.unwrap()
    return None


# ---------------------------------------------------------------------------
# Number list (faithful to _number_list / _number_with_suffix).
# ---------------------------------------------------------------------------


def _number_with_suffix(scan: _Scan) -> Optional[NumSuffix]:
    n = _read(scan, _NUM)
    if n is None:
        return None
    sf = _read(scan, _LETTER)
    return (n.text, sf.lemma if sf is not None else "")


def _number(scan: _Scan) -> Optional[str]:
    n = _read(scan, _NUM)
    return n.text if n is not None else None


def _letter(scan: _Scan) -> Optional[str]:
    t = _read(scan, _LETTER)
    return t.lemma if t is not None else None


_SEP_PRECEDERS = ("MOMENTTI", "KOHTA", "PYKALA", "LUKU", "OSA", "OTSIKKO", "JOHD", "LIITE")


def _skip_sentinels(scan: _Scan) -> None:
    """Advance past consecutive sentinel-span tokens (trivia around targets)."""
    while (t := scan.peek()) and t.cat in _SENTINEL_CATS:
        scan.advance()


def _sep(scan: _Scan) -> Optional[Token]:
    """The list separator, a faithful port of ``surface_parse._sep``.

    Skips sentinel spans before the separator (a provenance/citation span may
    sit between two list items), then matches COMMA / CONJ clusters — absorbing
    a provenance span flanked by commas and the archaic 'a'-as-conjunction.

    Faithful side effect (load-bearing for witness spans): on a leading sentinel
    skip followed by no separator, the position is left ADVANCED past the
    sentinel and None is returned WITHOUT rewinding — exactly as the old parser
    does, so a witness end position computed right after a failed trailing
    separator includes the swallowed sentinel. Callers that need to rewind must
    save/restore themselves.
    """
    sep_start = scan.pos
    _skip_sentinels(scan)
    skipped_sentinel = scan.pos > sep_start
    t = scan.peek()
    if t is None:
        return None

    # Sentinel-as-separator: a span between two targets where the comma was
    # absorbed into the span's edge. When a sentinel was skipped and a fresh
    # structural target resumes (NUM -> §/luku/...), the span is the separator.
    if skipped_sentinel and t.cat == "NUM":
        toks = scan.cur.tokens
        for k in range(scan.pos + 1, min(scan.pos + 4, len(toks))):
            kt = toks[k]
            if kt.cat in ("PYKALA", "LUKU", "LIITE", "NIMIKE", "MOMENTTI", "KOHTA"):
                return toks[sep_start]
            if kt.cat not in ("NUM", "LETTER", "DASH", "CONJ"):
                break

    if t.cat == "COMMA":
        scan.advance()
        # A provenance/citation span flanked by commas is transparent to list
        # separation: absorb "[sentinel] [,] [CONJ]*".
        if (sp_t := scan.peek()) and sp_t.cat in _SENTINEL_CATS:
            _skip_sentinels(scan)
            if (t2 := scan.peek()) and t2.cat == "COMMA":
                scan.advance()
        # Faithful quirk (load-bearing for witness spans): the walrus rebinds
        # ``t`` to the lookahead, so when the absorbed cluster is followed by
        # end-of-stream (or a non-CONJ token) this returns that token — possibly
        # None — even though the cursor has advanced past the comma + span. The
        # caller breaks on a None return WITHOUT rewinding, so the preceding
        # node's witness end position includes the swallowed trailing sentinels.
        while (t := scan.peek()) and t.cat == "CONJ":
            scan.advance()
        return t
    if t.cat == "CONJ":
        scan.advance()
        while (t2 := scan.peek()) and t2.cat == "CONJ":
            scan.advance()
        if (t2 := scan.peek()) and t2.cat == "COMMA":
            scan.advance()
            while (t3 := scan.peek()) and t3.cat == "CONJ":
                scan.advance()
            return t2
        return t
    # Archaic 'a' as conjunction (pre-1980s): only after a structural token and
    # before a NUM.
    if (
        t.cat == "LETTER"
        and t.lemma == "a"
        and scan.pos > 0
        and scan.cur.tokens[scan.pos - 1].cat in _SEP_PRECEDERS
        and (t1 := scan.peek(1)) is not None
        and t1.cat == "NUM"
    ):
        scan.advance()
        return t
    return None


def _number_list(scan: _Scan) -> Optional[list[NumSuffix]]:
    """comma/conj/dash-separated list of numbers with optional suffixes."""
    first = _number_with_suffix(scan)
    if first is None:
        return None
    results = [first]
    while True:
        saved = scan.pos
        if _read(scan, _DASH) is not None:
            end = _number_with_suffix(scan)
            if end is None:
                let = _letter(scan)
                if let and results:
                    end = (results[-1][0], let)
                else:
                    scan.goto(saved)
                    break
            expanded = _expand_range(results[-1], end)
            if len(expanded) > 1:
                results.pop()
                results.extend(expanded)
            else:
                results.append(end)
            continue
        if _sep(scan) is not None:
            nxt = _number_with_suffix(scan)
            if nxt is None:
                scan.goto(saved)
                break
            results.append(nxt)
            continue
        break
    return results


# ---------------------------------------------------------------------------
# Explicit scope context (faithful to _part_ctx / _chapter_ctx).
# ---------------------------------------------------------------------------


def _part_ctx(scan: _Scan) -> Optional[str]:
    """Parse 'N osan' — part context in genitive."""
    saved = scan.pos
    n = _number(scan)
    if n is None:
        return None
    if _read(scan, _OSA_GEN) is not None:
        return n
    scan.goto(saved)
    return None


def _chapter_ctx(scan: _Scan) -> Optional[str]:
    """Parse 'N luvun' — chapter context in genitive (optional letter suffix)."""
    saved = scan.pos
    n = _number(scan)
    if n is None:
        return None
    sf = _letter(scan) or ""
    if _read(scan, _LUKU_GEN) is not None:
        return n + sf
    scan.goto(saved)
    return None


# ---------------------------------------------------------------------------
# Top-level section-reference recognizers.
# ---------------------------------------------------------------------------


def _same_item_alakohta_continuation(scan: _Scan, subs: list["SubRef"]) -> Optional[list["SubRef"]]:
    """Parse ``ja i alakohta`` as a sibling under the previous item target."""
    parent = next((sr for sr in reversed(subs) if sr.item), None)
    if parent is None:
        return None

    saved = scan.pos
    labels = _letter_list(scan)
    if not labels:
        nums = _number_list(scan)
        labels = [n + sf for n, sf in nums] if nums else None
    if labels:
        _read(scan, _DASH)
    if labels and (t := scan.peek()) and t.cat == "ALAKOHTA":
        scan.advance()
        return [
            SubRef(momentti=parent.momentti, item=parent.item, subitem=label)
            for label in labels
        ]

    scan.goto(saved)
    return None


def recognize_section_ref(scan: _Scan) -> Optional[ParsedSection]:
    """Recognize the suffix / renumber forms: [part][chapter] numlist § [sub].

    Mirrors ``surface_parse._section_ref``. Returns a ``ParsedSection`` (the
    intermediate) or None (recoverable: the caller restores and tries another
    form).
    """
    start = scan.pos
    explicit_pt = _part_ctx(scan)
    explicit_ch = _chapter_ctx(scan)
    nums = _number_list(scan)
    if not nums:
        scan.goto(start)
        return None
    t = scan.peek()
    if not (t and t.cat == "PYKALA" and t.case != "ILL"):
        scan.goto(start)
        return None
    pyk_is_gen = t.case == "GEN"  # "§:n" — a sub-provision may follow
    pyk_pos = scan.pos  # § position, for the parallel-pair backward scan
    scan.advance()  # consume §

    last_sep_is_conj = _scan_last_sep_is_conj(scan.cur.tokens, pyk_pos)

    # ── Renumber arm: "§:n numero N:ksi" ───────────────────────────────────
    if (t := scan.peek()) and t.cat == "NUMERO":
        scan.advance()
        renumber_targets = _renumber_target_list(scan) or []
        base_span_end = scan.pos
        # An optional ``(ja|,) mainitun pykälän …`` anaphoric back-reference
        # tail: the old ``_section_ref`` consumes it inline and re-emits the same
        # renumber sources a second time with a ``renumber_backref_clause`` note.
        renumber_backref_subs = _parse_renumber_backref_continuation(scan) or []
        return ParsedSection(
            form=SectionForm.RENUMBER,
            span=Span(start, scan.pos),
            nums=tuple(nums),
            explicit_chapter=explicit_ch,
            explicit_part=explicit_pt,
            renumber_targets=tuple(renumber_targets),
            renumber_backref_subs=tuple(renumber_backref_subs),
            renumber_base_span_end=(
                base_span_end if renumber_backref_subs else None
            ),
        )

    # ── Suffix arm: sub-refs ────────────────────────────────────────────────
    # The content-named ``<descriptor> kohta`` arm is offered ONLY at this
    # immediate post-``§:n`` position (and only when the § was genitive, i.e. a
    # sub-provision genuinely follows). It is NOT offered inside the same-section
    # separator loop below or after a nominative ``§``: there a ``ja <WORD> …
    # kohta`` run is a *coordinated separate target* (``2 § ja liitteessä 1
    # olevan … kohta``), not a sub-ref of this section.
    subs = _sub_ref(scan, allow_descriptor=pyk_is_gen)
    if not subs:
        # "N §:n edelle uusi … otsikko" is a heading placement, not a section
        # ref — back out so the driver treats the clause as out of scope.
        if (t := scan.peek()) and t.cat == "EDELLA":
            scan.goto(start)
            return None
        subs = [SubRef()]  # whole section

    # Additional same-section sub-refs joined by separators.
    while True:
        saved2 = scan.pos
        if _sep(scan) is None:
            break
        after_sep = scan.pos
        more = _sub_ref(scan)
        if not more:
            scan.goto(after_sep)
            more = _same_item_alakohta_continuation(scan, subs)
        if more:
            subs.extend(more)
        else:
            scan.goto(saved2)
            break

    return ParsedSection(
        form=SectionForm.SUFFIX,
        span=Span(start, scan.pos),
        nums=tuple(nums),
        explicit_chapter=explicit_ch,
        explicit_part=explicit_pt,
        subs=tuple(subs),
        last_sep_is_conj=last_sep_is_conj,
    )


def _scan_last_sep_is_conj(tokens, pyk_pos: int) -> bool:
    """Backward scan from § over NUM/LETTER: True iff a CONJ precedes a COMMA.

    Faithful to ``surface_parse._section_ref``'s parallel-pair test: starting
    just before §, skip NUM/LETTER tokens; the first CONJ found means the two
    numbers are a parallel pair (both scoped), the first COMMA means they are
    not (the leading number is whole-section). Anything else stops the scan.
    """
    for idx in range(pyk_pos - 1, -1, -1):
        tk = tokens[idx]
        if tk.cat in ("CONJ", "SEKA"):
            return True
        if tk.cat == "COMMA":
            return False
        if tk.cat in ("NUM", "LETTER"):
            continue
        break
    return False


def _renumber_number_with_suffix(scan: _Scan) -> Optional[tuple[NumSuffix, bool]]:
    """Renumber target number + optional suffix, tracking translative closure."""
    t = scan.peek()
    if t is None or t.cat != "NUM":
        return None
    scan.advance()
    sf = _letter(scan) or ""
    n = t.lemma if t.case == "TRANS" else t.text
    return (n, sf), (t.case == "TRANS")


def _renumber_target_list(scan: _Scan) -> Optional[list[NumSuffix]]:
    """Parse translative renumber targets (faithful to _renumber_target_list)."""
    saved_start = scan.pos
    first_data = _renumber_number_with_suffix(scan)
    if first_data is None:
        return None
    first, first_trans = first_data
    results = [first]
    saw_trans = first_trans
    while True:
        saved = scan.pos
        t = scan.peek()
        if not (t and t.cat in ("CONJ", "DASH")):
            break
        if t.cat == "DASH":
            scan.advance()
            end_data = _renumber_number_with_suffix(scan)
            if end_data is None:
                scan.goto(saved)
                break
            end, end_trans = end_data
            if not end_trans:
                scan.goto(saved)
                break
            expanded = _expand_range(results[-1], end)
            if len(expanded) > 1:
                results.pop()
                results.extend(expanded)
            else:
                results.append(end)
            saw_trans = True
            continue

        _sep(scan)
        more_data = _renumber_number_with_suffix(scan)
        if more_data is None:
            scan.goto(saved)
            break
        more, more_trans = more_data
        if not more_trans:
            if _read(scan, _DASH) is None:
                scan.goto(saved)
                break
            end_data = _renumber_number_with_suffix(scan)
            if end_data is None:
                scan.goto(saved)
                break
            end, end_trans = end_data
            if not end_trans:
                scan.goto(saved)
                break
            expanded = _expand_range(more, end)
            if len(expanded) > 1:
                results.extend(expanded)
            else:
                results.extend([more, end])
            saw_trans = True
            continue
        results.append(more)
        saw_trans = True
    if not saw_trans:
        scan.goto(saved_start)
        return None
    return results


def _parse_renumber_backref_continuation(scan: _Scan) -> Optional[list[SubRef]]:
    """Consume a trailing ``[sep] mainitun pykälän [sub_ref …]`` after a renumber.

    Faithful to ``surface_parse._parse_backref_continuation`` as invoked inline by
    ``_section_ref``'s NUMERO branch: an optional list separator, then a BACKREF
    determiner, ``pykälä``/``pykälän``/``pykälien``/``pykälät``, then the trailing
    sub-references (an absent sub-ref becomes one whole-section ``SubRef()``).  The
    leading separator is consumed here (not by the driver) so the renumber arm's
    span extends across the back-reference, byte-identically to the old parser.
    Returns the sub-refs, or ``None`` (rewinding ``scan``) on no match.
    """
    saved = scan.pos
    _sep(scan)
    if _read(scan, _BACKREF) is None:
        scan.goto(saved)
        return None
    if _read(scan, _PYKALA) is None:
        scan.goto(saved)
        return None
    subs = _sub_ref(scan)
    if subs:
        while True:
            saved2 = scan.pos
            if _sep(scan) is None:
                break
            more = _sub_ref(scan)
            if more:
                subs.extend(more)
            else:
                scan.goto(saved2)
                break
    if not subs:
        subs = [SubRef()]  # whole section ("mainittu pykälä")
    # Trailing kohta-level facet distribution (same as the section-suffix path).
    if len(subs) > 1 and subs[-1].facet is not None and subs[-1].item:
        trailing_facet = subs[-1].facet
        for i in range(len(subs) - 1):
            if subs[i].facet is None and subs[i].item:
                subs[i] = SubRef(
                    momentti=subs[i].momentti,
                    item=subs[i].item,
                    subitem=subs[i].subitem,
                    facet=trailing_facet,
                )
    return subs


def recognize_pykala_prefix_section_ref(scan: _Scan) -> Optional[ParsedSection]:
    """Recognize ``pykälien <numlist>`` (genitive-plural prefix form).

    Mirrors ``surface_parse._pykala_prefix_section_ref``.
    """
    start = scan.pos
    t = scan.peek()
    if not (t and t.cat == "PYKALA" and t.case == "GEN"):
        return None
    if (t.text or "").lower() != "pykälien":
        return None
    scan.advance()
    nums = _number_list(scan)
    if not nums:
        scan.goto(start)
        return None
    if (nxt := scan.peek()) and nxt.cat in ("PYKALA", "MOMENTTI", "KOHTA", "LUKU", "OSA"):
        scan.goto(start)
        return None
    return ParsedSection(
        form=SectionForm.PYKALA_PREFIX,
        span=Span(start, scan.pos),
        nums=tuple(nums),
    )


# ---------------------------------------------------------------------------
# Emitter — ParsedSection -> frozen Surface* nodes (faithful to the old
# parser's node construction).
# ---------------------------------------------------------------------------


def _to_surface_sub_refs(subs: list[SubRef]) -> tuple[SurfaceSubRef, ...]:
    """Convert recognizer SubRefs to frozen SurfaceSubRefs (legacy special)."""
    out: list[SurfaceSubRef] = []
    for sr in subs:
        special = sr.special
        if sr.facet == FacetKind.HEADING:
            special = "otsikko"
        elif sr.facet == FacetKind.INTRO:
            special = "johd"
        out.append(
            SurfaceSubRef(
                momentti=sr.momentti,
                item=sr.item,
                subitem=sr.subitem,
                facet=sr.facet,
                special=special,
            )
        )
    return tuple(out)


def _maybe_wrap_scope_block(
    nodes: list[SurfaceNode], scope_ch: str, scope_pt: str, span: Span
) -> list[SurfaceNode]:
    """Wrap target nodes in a SurfaceScopeBlock when an explicit scope parsed."""
    if not scope_ch and not scope_pt:
        return nodes
    if not nodes:
        return nodes
    if not all(isinstance(n, SurfaceTargetRef) for n in nodes):
        return nodes
    target_nodes = [n for n in nodes if isinstance(n, SurfaceTargetRef)]
    scope_kind = ScopeKind.PART if scope_pt else ScopeKind.CHAPTER
    scope_label = scope_pt if scope_pt else scope_ch
    w = SurfaceWitness(
        rule_id=f"fi.scope_block_{scope_kind.value}", source_span=(span.start, span.end)
    )
    return [
        SurfaceScopeBlock(
            scope_kind=scope_kind,
            scope_label=scope_label,
            targets=tuple(target_nodes),
            witness=w,
        )
    ]


def _emit_renumber(parsed: ParsedSection, chapter: str, part: str) -> list[SurfaceNode]:
    """Emit the renumber arm (faithful to _section_ref's NUMERO branch)."""
    explicit_ch = parsed.explicit_chapter
    explicit_pt = parsed.explicit_part
    ch = explicit_ch if explicit_ch is not None else chapter
    pt = explicit_pt if explicit_pt is not None else part
    nums = list(parsed.nums)

    source_labels: list[str] = []
    for n, sf in nums:
        expanded = _expand_range_single(n)
        if len(expanded) == 1:
            source_labels.append(expanded[0] + sf)
        else:
            source_labels.extend(expanded)
    destination_labels: list[str] = []
    for n, sf in parsed.renumber_targets:
        expanded = _expand_range_single(n)
        if len(expanded) == 1:
            destination_labels.append((expanded[0] + sf).removesuffix(":ksi"))
        else:
            destination_labels.extend(label.removesuffix(":ksi") for label in expanded)
    destination_by_source = (
        dict(zip(source_labels, destination_labels, strict=True))
        if len(source_labels) == len(destination_labels)
        else {}
    )

    scope_ch = ch if explicit_ch is not None and explicit_pt is None else ""
    scope_pt = pt if explicit_pt is not None else ""
    target_ch = "" if (scope_ch and not scope_pt) else ch
    target_pt = "" if scope_pt else pt

    # The old parser stamps the base renumber nodes with the renumber span as it
    # stood BEFORE consuming any ``mainitun pykälän …`` tail; the back-reference
    # nodes get the full (post-tail) span.
    base_end = (
        parsed.renumber_base_span_end
        if parsed.renumber_base_span_end is not None
        else parsed.span.end
    )
    w = SurfaceWitness(rule_id="fi.section_renumber", source_span=(parsed.span.start, base_end))
    nodes: list[SurfaceNode] = []
    for n, sf in nums:
        for rn in _expand_range_single(n):
            full = rn + (sf if len(_expand_range_single(n)) == 1 else "")
            dest_label = destination_by_source.get(full, "")
            nodes.append(
                SurfaceTargetRef(
                    kind=TargetKind.SECTION,
                    label=full,
                    chapter=target_ch,
                    part=target_pt,
                    sub_refs=(),
                    notes=("renumber_clause",),
                    renumber_dest=dest_label,
                    witness=w,
                )
            )
    # Re-emit each renumber source a second time, scoped to the trailing
    # ``mainitun pykälän …`` back-reference sub-refs, with the extra
    # ``renumber_backref_clause`` note (faithful to _section_ref's NUMERO branch).
    if parsed.renumber_backref_subs:
        backref_w = SurfaceWitness(
            rule_id="fi.section_renumber", source_span=(parsed.span.start, parsed.span.end)
        )
        backref_sub_refs = _to_surface_sub_refs(list(parsed.renumber_backref_subs))
        for n, sf in nums:
            for rn in _expand_range_single(n):
                full = rn + (sf if len(_expand_range_single(n)) == 1 else "")
                dest_label = destination_by_source.get(full, "")
                nodes.append(
                    SurfaceTargetRef(
                        kind=TargetKind.SECTION,
                        label=full,
                        chapter=target_ch,
                        part=target_pt,
                        sub_refs=backref_sub_refs,
                        notes=("renumber_clause", "renumber_backref_clause"),
                        renumber_dest=dest_label,
                        witness=backref_w,
                    )
                )
    return _maybe_wrap_scope_block(nodes, scope_ch, scope_pt, parsed.span)


def _emit_suffix(parsed: ParsedSection, chapter: str, part: str) -> list[SurfaceNode]:
    """Emit the suffix arm (faithful to the body of _section_ref)."""
    explicit_ch = parsed.explicit_chapter
    explicit_pt = parsed.explicit_part
    ch = explicit_ch if explicit_ch is not None else chapter
    pt = explicit_pt if explicit_pt is not None else part
    nums = list(parsed.nums)
    subs = [
        SubRef(
            momentti=sr.momentti,
            item=sr.item,
            subitem=sr.subitem,
            facet=sr.facet,
            special=sr.special,
        )
        for sr in parsed.subs
    ]

    # Trailing facet distribution (kohta level).
    if len(subs) > 1 and subs[-1].facet is not None and subs[-1].item:
        trailing_facet = subs[-1].facet
        for i in range(len(subs) - 1):
            if subs[i].facet is None and subs[i].item:
                subs[i] = SubRef(
                    momentti=subs[i].momentti,
                    item=subs[i].item,
                    subitem=subs[i].subitem,
                    facet=trailing_facet,
                )

    # Trailing sub-ref scoping: leading whole-section split.
    leading_whole: list[NumSuffix] = []
    scoped_nums = nums
    if subs and subs != [SubRef()] and len(nums) > 1:
        _parallel_pair = len(nums) == 2 and parsed.last_sep_is_conj
        if not _parallel_pair:
            leading_whole = nums[:-1]
            scoped_nums = [nums[-1]]

    # Propagate momentti context to bare kohta items.
    last_mom = 0
    for i, sr in enumerate(subs):
        if sr.momentti != 0:
            last_mom = sr.momentti
        elif sr.item and last_mom != 0:
            subs[i] = SubRef(
                momentti=last_mom,
                item=sr.item,
                subitem=sr.subitem,
                facet=sr.facet,
                special=sr.special,
            )
    # Default bare items to momentti=1.
    subs = [
        SubRef(
            momentti=m if m != 0 or not sr.item else 1,
            item=sr.item,
            subitem=sr.subitem,
            facet=sr.facet,
            special=sr.special,
        )
        for sr in subs
        for m in [sr.momentti]
    ]

    scope_ch = ch if explicit_ch is not None and explicit_pt is None else ""
    scope_pt = pt if explicit_pt is not None else ""
    has_explicit_scope = bool(scope_ch or scope_pt)
    target_ch = "" if (scope_ch and not scope_pt) else ch
    target_pt = "" if scope_pt else pt

    w = SurfaceWitness(rule_id="fi.section_ref", source_span=(parsed.span.start, parsed.span.end))
    nodes: list[SurfaceNode] = []
    for n, sf in leading_whole:
        for rn in _expand_range_single(n):
            full = rn + (sf if len(_expand_range_single(n)) == 1 else "")
            nodes.append(
                SurfaceTargetRef(
                    kind=TargetKind.SECTION,
                    label=full,
                    chapter=target_ch,
                    part=target_pt,
                    witness=w,
                )
            )
    for n, sf in scoped_nums:
        for rn in _expand_range_single(n):
            full = rn + (sf if len(_expand_range_single(n)) == 1 else "")
            surface_subs = _to_surface_sub_refs(subs)
            if len(surface_subs) >= 2 and not has_explicit_scope:
                base = SurfaceTargetRef(
                    kind=TargetKind.SECTION,
                    label=full,
                    chapter=ch,
                    part=pt,
                    sub_refs=(),
                    witness=w,
                )
                nodes.append(SurfaceDescendantCoordination(base=base, arms=surface_subs, witness=w))
            else:
                nodes.append(
                    SurfaceTargetRef(
                        kind=TargetKind.SECTION,
                        label=full,
                        chapter=target_ch,
                        part=target_pt,
                        sub_refs=surface_subs,
                        witness=w,
                    )
                )
    return _maybe_wrap_scope_block(nodes, scope_ch, scope_pt, parsed.span)


def emit_section_nodes(parsed: ParsedSection, chapter: str = "", part: str = "") -> list[SurfaceNode]:
    """Turn a recognized ``ParsedSection`` into frozen Surface* nodes.

    ``chapter`` / ``part`` are the inherited (non-explicit) context; in the
    slice-1 subset these are always empty (cross-verb-group inheritance is out
    of scope), but the parameters preserve the old parser's signature so the
    emit logic stays faithful.
    """
    if parsed.form is SectionForm.RENUMBER:
        return _emit_renumber(parsed, chapter, part)
    if parsed.form is SectionForm.PYKALA_PREFIX:
        return _emit_pykala_prefix(parsed, chapter, part)
    return _emit_suffix(parsed, chapter, part)


def _emit_pykala_prefix(parsed: ParsedSection, chapter: str, part: str) -> list[SurfaceNode]:
    """Emit whole-section targets for the genitive-plural prefix form."""
    w = SurfaceWitness(
        rule_id="fi.section_ref_pykala_prefix", source_span=(parsed.span.start, parsed.span.end)
    )
    nodes: list[SurfaceNode] = []
    for n, sf in parsed.nums:
        for rn in _expand_range_single(n):
            full = rn + (sf if len(_expand_range_single(n)) == 1 else "")
            nodes.append(
                SurfaceTargetRef(
                    kind=TargetKind.SECTION,
                    label=full,
                    chapter=chapter,
                    part=part,
                    witness=w,
                )
            )
    return nodes


# ---------------------------------------------------------------------------
# Intra-group context carry-forward (narrowed to the section family).
#
# Within one verb group the old parser propagates the chapter/part scope from
# one target batch to the next so a later bare section list ("... ja 13 ja
# 14 §") inherits the preceding "30 luvun" scope. This is intra-group surface
# structure, not cross-verb-group resolution: each is a faithful narrowing of
# surface_parse._extract_chapter_from_nodes / _extract_part_from_nodes
# restricted to the node types this family emits (SECTION targets, chapter/part
# scope blocks, descendant coordinations). Whole-chapter / whole-part targets,
# headings and insertions never occur in this subset, so their branches are
# omitted.
# ---------------------------------------------------------------------------


def extract_chapter(nodes: list[SurfaceNode], current: str) -> str:
    """The chapter scope carried forward from a section-family target batch."""
    for node in reversed(nodes):
        if isinstance(node, SurfaceScopeBlock):
            if node.scope_kind == ScopeKind.CHAPTER and node.scope_label:
                return node.scope_label
        elif isinstance(node, SurfaceDescendantCoordination):
            if node.base.chapter:
                return node.base.chapter
        elif isinstance(node, SurfaceTargetRef):
            if node.chapter:
                return node.chapter
    return current


def _is_heading_only_target(node: SurfaceTargetRef) -> bool:
    """True if a target amends only the target heading facet (old sp:1145)."""
    return bool(node.sub_refs) and all(
        sr.facet == FacetKind.HEADING and sr.momentti == 0 and not sr.item
        for sr in node.sub_refs
    )


def extract_part(nodes: list[SurfaceNode], current: str) -> str:
    """The part scope carried forward from a target batch.

    A faithful narrowing of ``surface_parse._extract_part_from_nodes`` for the
    node types the wired families pass through here: scope blocks, descendant
    coordinations, and SurfaceTargetRefs — including the whole-PART target a
    container batch emits (``III ja V osa``), whose *label* is the carried part
    scope just as a whole-chapter target's label is the carried chapter scope.

    A part's OWN heading (``II osan otsikko``, kind=PART, heading-only sub-refs)
    names a target, not a scope, so it does not leak its label forward. A chapter
    heading under an explicit part prefix (``II osan 4 luvun otsikko``) carries
    that part forward — but only when the batch does not ALSO amend a part heading
    (then the part label is a target, not a scope).
    """
    if _is_coordinated_part_heading_batch(nodes):
        return current
    batch_amends_part_heading = any(
        isinstance(node, SurfaceTargetRef)
        and node.kind == TargetKind.PART
        and _is_heading_only_target(node)
        for node in nodes
    )
    for node in reversed(nodes):
        if isinstance(node, SurfaceScopeBlock):
            if node.scope_kind == ScopeKind.PART and node.scope_label:
                return node.scope_label
        elif isinstance(node, SurfaceDescendantCoordination):
            if node.base.part:
                return node.base.part
        elif isinstance(node, SurfaceTargetRef):
            if _is_heading_only_target(node):
                # A part's own heading must not leak its label forward; a chapter
                # heading under a part prefix carries that part forward unless the
                # batch also amends a part heading.
                if (
                    node.kind != TargetKind.PART
                    and node.part
                    and not batch_amends_part_heading
                ):
                    return node.part
                continue
            # A whole-PART target (``V osa``) carries its label forward as scope.
            if node.kind == TargetKind.PART and node.label:
                return node.label
            if node.part:
                return node.part
    return current


def _is_coordinated_part_heading_batch(nodes: list[SurfaceNode]) -> bool:
    """Whether a batch is the coordinated ``N osan ja M luvun otsikko`` shape.

    That shape emits a leading PART heading target with witness
    ``fi.coordinated_part_chapter_heading_ref`` followed by a CHAPTER heading
    target that carries the same part label only to express *part N's M-th
    chapter heading*. The two are sibling heading targets joined by ``ja``; the
    part scope is local to the coordination and must NOT leak onto a following
    independent target batch. The non-coordinated context-prefix shape
    (``N osan M luvun otsikko``, no ``ja``) carries no such witness and its part
    DOES scope forward, so it is left to carry normally.
    """
    return any(
        isinstance(n, SurfaceTargetRef)
        and n.witness is not None
        and n.witness.rule_id == "fi.coordinated_part_chapter_heading_ref"
        for n in nodes
    )


# ---------------------------------------------------------------------------
# Sub-reference recognizer cluster — relocated to ``grammar.subref``.
#
# Imported HERE (module bottom) rather than at the top so the low-level scanner
# and number-list helpers ``subref`` depends on (``_Scan`` / ``_number_list`` /
# ``_letter`` / ``_expand_range_single`` / ``_read`` / ``_sep`` / ``_DASH``) are
# already defined when ``subref`` imports them, breaking the import cycle. These
# re-imports keep ``SubRef`` / ``_sub_ref`` / … accessible as attributes of this
# module so external callers (``from ...grammar import sections as S``) and the
# runtime references in the recognizers/emitters above resolve unchanged.
# ---------------------------------------------------------------------------

from lawvm.finland.johtolause.grammar.subref import (  # noqa: E402,F401
    SubRef,
    _clause_has_multi_section_heading_insert,
    _DESCRIPTOR_STOPWORDS,
    _full_label,
    _letter_list,
    _named_descriptor_subref,
    _parse_after_gen_kohta,
    _parse_descendant_coordination,
    _sub_ref,
)
