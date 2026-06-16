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

# ---------------------------------------------------------------------------
# Intermediate sub-reference (mirrors surface_parse.SubRef, recognizer-local).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SubRef:
    """A parsed sub-reference: momentti, item, or facet (pre-emit form)."""

    momentti: int = 0  # 0 = whole section
    item: str = ""
    facet: Optional[FacetKind] = None


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


def _full_label(num: str, suffix: str) -> str:
    """The label rule: keep the suffix only when the number is not a range."""
    expanded = _expand_range_single(num)
    return num + (suffix if len(expanded) == 1 else "")


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


def _letter_list(scan: _Scan) -> Optional[list[str]]:
    """comma/conj/dash-separated list of item letters (with letter ranges)."""
    first = _letter(scan)
    if first is None:
        return None
    results = [first]
    while True:
        saved = scan.pos
        if _read(scan, _DASH) is not None:
            end = _letter(scan)
            if end is None:
                scan.goto(saved)
                break
            if len(results[-1]) == 1 and len(end) == 1:
                a, b = ord(results[-1]), ord(end)
                if a <= b and b - a < 26:
                    results.pop()
                    results.extend(chr(c) for c in range(a, b + 1))
                    continue
            results.append(end)
            continue
        if _sep(scan) is not None:
            nxt = _letter(scan)
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
# Sub-reference recognition (faithful to _sub_ref /
# _parse_descendant_coordination, restricted to the section-ref subset).
# ---------------------------------------------------------------------------


def _parse_after_gen_kohta(scan: _Scan) -> Optional[FacetKind]:
    t = scan.peek()
    if t and t.cat == "JOHD":
        scan.advance()
        return FacetKind.INTRO
    return None


def _parse_descendant_coordination(scan: _Scan, mom_ctx: int = 0) -> Optional[list[SubRef]]:
    """Recursive descendant-coordination parser for sub-references."""
    saved = scan.pos

    nums = _number_list(scan)
    if not nums:
        scan.goto(saved)
        letters = _letter_list(scan)
        t2 = scan.peek()
        if letters and t2 and t2.cat == "KOHTA":
            is_kohta_gen = t2.case == "GEN"
            scan.advance()
            if is_kohta_gen:
                t3 = scan.peek()
                if t3 and t3.cat == "JOHD":
                    scan.advance()
                    return [SubRef(mom_ctx, let, facet=FacetKind.INTRO) for let in letters]
                if t3 and t3.cat == "OTSIKKO":
                    scan.advance()
                return [SubRef(mom_ctx, let) for let in letters]
            return [SubRef(momentti=mom_ctx, item=let) for let in letters]
        return None

    t2 = scan.peek()

    # ── MOMENTTI branch ───────────────────────────────────────────────────
    if t2 and t2.cat == "MOMENTTI":
        is_gen = t2.case == "GEN"
        scan.advance()

        if is_gen:
            mom_vals: list[int] = []
            for n, _sf in nums:
                for rn in _expand_range_single(n):
                    mom_vals.append(int(rn) if rn.isdigit() else 0)

            saved_kohta = scan.pos
            knums = _number_list(scan)
            if knums and (t := scan.peek()) and t.cat == "KOHTA":
                is_kohta_gen2 = t.case == "GEN"
                scan.advance()
                if is_kohta_gen2:
                    facet = _parse_after_gen_kohta(scan)
                    return [
                        SubRef(mom, kn + ksf, facet=facet)
                        for mom in mom_vals
                        for kn, ksf in knums
                    ]
                return [SubRef(mom, kn + ksf) for mom in mom_vals for kn, ksf in knums]
            scan.goto(saved_kohta)

            saved_lk = scan.pos
            letters = _letter_list(scan)
            if letters and (t := scan.peek()) and t.cat == "KOHTA":
                scan.advance()
                return [SubRef(mom, let) for mom in mom_vals for let in letters]
            if letters:
                scan.goto(saved_lk)

            t3 = scan.peek()
            if t3 and t3.cat == "JOHD":
                scan.advance()
                return [SubRef(mom, facet=FacetKind.INTRO) for mom in mom_vals]
            if t3 and t3.cat == "OTSIKKO":
                scan.advance()
                return [SubRef(mom, facet=FacetKind.HEADING) for mom in mom_vals]

            return [SubRef(mom) for mom in mom_vals]

        # Nominative MOMENTTI
        result: list[SubRef] = []
        for n, _sf in nums:
            for rn in _expand_range_single(n):
                result.append(SubRef(momentti=int(rn) if rn.isdigit() else 0))
        return result

    # ── KOHTA branch ──────────────────────────────────────────────────────
    if t2 and t2.cat == "KOHTA":
        is_kohta_gen = t2.case == "GEN"
        scan.advance()

        if is_kohta_gen:
            t3 = scan.peek()
            if t3 and t3.cat == "JOHD":
                scan.advance()
                return [SubRef(mom_ctx, n + sf, facet=FacetKind.INTRO) for n, sf in nums]
            if t3 and t3.cat == "OTSIKKO":
                scan.advance()  # consume but do not set facet
            return [SubRef(mom_ctx, n + sf) for n, sf in nums]

        return [SubRef(momentti=mom_ctx, item=n + sf) for n, sf in nums]

    # Numbers without a structural noun: not a valid sub-ref.
    scan.goto(saved)
    return None


def _clause_has_multi_section_heading_insert(scan: _Scan) -> bool:
    """True iff the clause carries a ``N [letter] (ja|,|–) M [letter] §:n edelle
    uusi (väli)otsikko`` heading-INSERT spanning two or more sections.

    Such a multi-section heading-insert is an insertion-family shape the
    integrated pipeline does not yet expand identically to the old parser when
    it appears in a later verb group (the old parser drops the trailing chained
    insert there; the new insertion family keeps it). Recovering a heading-CHANGE
    in the SAME clause would unblock the clause and surface that downstream
    insertion divergence as a miscompile, so the heading-change arm declines when
    this shape is present — fail-loud rather than emit a divergent model. A
    single-section heading-insert (the common case) does NOT match and is left
    untouched. Scans the whole token stream (recognizer-local; read-only).
    """
    tokens = scan.cur.tokens
    n = len(tokens)
    for i in range(n):
        tk = tokens[i]
        if tk.cat != "EDELLA" or (tk.text or "").lower() != "edelle":
            continue
        if i == 0 or tokens[i - 1].cat != "PYKALA":
            continue
        # Backward count of NUM groups feeding this §, requiring a separator.
        nums = 0
        saw_sep = False
        k = i - 2
        while k >= 0 and tokens[k].cat in ("NUM", "LETTER", "CONJ", "COMMA", "DASH", "SEKA"):
            if tokens[k].cat == "NUM":
                nums += 1
            elif tokens[k].cat in ("CONJ", "COMMA", "DASH", "SEKA"):
                saw_sep = True
            k -= 1
        if nums < 2 or not saw_sep:
            continue
        # ``edelle uusi (väli)otsikko`` within the next few tokens.
        window = tokens[i + 1 : i + 5]
        if any(w.cat == "UUSI" for w in window) and any(
            w.cat == "OTSIKKO" for w in window
        ):
            return True
    return False


def _sub_ref(scan: _Scan) -> Optional[list[SubRef]]:
    """Parse a sub-reference after a § token (slice-1 subset).

    Recognizes the section-level facets (otsikko / johdantokappale), the
    ``edellä oleva/olevien otsikko`` heading-CHANGE reference, and the
    momentti/kohta descendant coordination. The heading-INSERT form
    ``N §:n edelle uusi väliotsikko`` (allative ``edelle`` + ``uusi``) is NOT
    recognized here — that is a heading placement, not a section ref, and the
    caller backs out of the whole section ref on a bare 'edelle' lookahead.
    """
    saved = scan.pos

    t = scan.peek()
    if t and t.cat == "OTSIKKO":
        scan.advance()
        return [SubRef(facet=FacetKind.HEADING)]
    if t and t.cat == "JOHD":
        scan.advance()
        return [SubRef(facet=FacetKind.INTRO)]

    # "edellä oleva (luvun) otsikko" — heading-CHANGE reference (locative
    # "edellä" + participle of "olla"), distinct from the heading-INSERT form
    # "N §:n edelle uusi väliotsikko" (allative "edelle" + "uusi") which the
    # caller backs out of. The change form binds the preceding section number as
    # a heading-amend target (HEADING facet) so the enclosing kumotaan/muutetaan
    # list keeps going. Faithful to surface_parse._sub_ref.
    if t and t.cat == "EDELLA" and (t.text or "").lower() == "edellä":
        # A multi-section heading-INSERT elsewhere in the clause is an
        # insertion-family shape the integrated pipeline does not yet expand
        # identically to the old parser. Recovering this heading-CHANGE would
        # unblock the clause and surface that downstream divergence as a
        # miscompile, so decline (back out to the bare 'edellä' the caller's
        # guard rejects) rather than emit a divergent model.
        if _clause_has_multi_section_heading_insert(scan):
            return None
        saved_e = scan.pos
        scan.advance()
        nxt = scan.peek()
        if nxt and nxt.lemma == "olla":
            # "edellä oleva [luvun] otsikko" — optional LUKU genitive
            # ("luvun otsikko" = the chapter heading preceding the section).
            scan.advance()
            if (t2 := scan.peek()) and t2.cat == "LUKU":
                scan.advance()
            if (t2 := scan.peek()) and t2.cat == "OTSIKKO":
                scan.advance()
                # When a ``(ja|,) mainitun pykälän …`` anaphoric backref
                # continuation immediately follows the heading change, the old
                # parser routes the backref through its ``_parse_backref_
                # continuation`` arm, which folds the leading separator into the
                # backref node's span START. The integrated driver instead
                # consumes that separator before reaching the backref, so the
                # backref node's span would diverge by one token. Back out so the
                # clause declines rather than emit a span-divergent backref —
                # faithful-or-decline, never miscompile.
                bt0 = scan.peek()
                bt1 = scan.peek(1)
                if (
                    bt0 is not None
                    and bt0.cat in ("CONJ", "COMMA")
                    and bt1 is not None
                    and bt1.cat == "BACKREF"
                ):
                    scan.goto(saved)
                    return None
                return [SubRef(facet=FacetKind.HEADING)]
        elif nxt and (nxt.text or "").lower() == "olevien":
            # Plural participle: "edellä olevien lukujen otsikoiden numerointi"
            # — a heading-renumbering reference distributed over the preceding
            # section list. Consume the descriptive tail up to "numerointi".
            tail_saved = scan.pos
            scan.advance()
            saw_otsikko = False
            while (t2 := scan.peek()) and t2.cat in ("LUKU", "OTSIKKO", "WORD"):
                if t2.cat == "OTSIKKO" or (t2.text or "").lower().startswith("otsiko"):
                    saw_otsikko = True
                consumed_numerointi = (t2.text or "").lower().startswith("numeroin")
                scan.advance()
                if consumed_numerointi:
                    break
            if saw_otsikko:
                return [SubRef(facet=FacetKind.HEADING)]
            scan.goto(tail_saved)
        scan.goto(saved_e)

    result = _parse_descendant_coordination(scan)
    if result is not None:
        return result

    # Letter + KOHTA: "H kohta" (no number prefix)
    let = _letter(scan)
    if let:
        if (t := scan.peek()) and t.cat == "KOHTA":
            scan.advance()
            return [SubRef(momentti=0, item=let)]
        scan.goto(saved)
        return None

    return None


# ---------------------------------------------------------------------------
# Top-level section-reference recognizers.
# ---------------------------------------------------------------------------


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
    pyk_pos = scan.pos  # § position, for the parallel-pair backward scan
    scan.advance()  # consume §

    last_sep_is_conj = _scan_last_sep_is_conj(scan.cur.tokens, pyk_pos)

    # ── Renumber arm: "§:n numero N:ksi" ───────────────────────────────────
    if (t := scan.peek()) and t.cat == "NUMERO":
        scan.advance()
        renumber_targets = _renumber_target_list(scan) or []
        return ParsedSection(
            form=SectionForm.RENUMBER,
            span=Span(start, scan.pos),
            nums=tuple(nums),
            explicit_chapter=explicit_ch,
            explicit_part=explicit_pt,
            renumber_targets=tuple(renumber_targets),
        )

    # ── Suffix arm: sub-refs ────────────────────────────────────────────────
    subs = _sub_ref(scan)
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
        more = _sub_ref(scan)
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
        special = ""
        if sr.facet == FacetKind.HEADING:
            special = "otsikko"
        elif sr.facet == FacetKind.INTRO:
            special = "johd"
        out.append(
            SurfaceSubRef(momentti=sr.momentti, item=sr.item, facet=sr.facet, special=special)
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

    w = SurfaceWitness(rule_id="fi.section_renumber", source_span=(parsed.span.start, parsed.span.end))
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
    return _maybe_wrap_scope_block(nodes, scope_ch, scope_pt, parsed.span)


def _emit_suffix(parsed: ParsedSection, chapter: str, part: str) -> list[SurfaceNode]:
    """Emit the suffix arm (faithful to the body of _section_ref)."""
    explicit_ch = parsed.explicit_chapter
    explicit_pt = parsed.explicit_part
    ch = explicit_ch if explicit_ch is not None else chapter
    pt = explicit_pt if explicit_pt is not None else part
    nums = list(parsed.nums)
    subs = [SubRef(sr.momentti, sr.item, sr.facet) for sr in parsed.subs]

    # Trailing facet distribution (kohta level).
    if len(subs) > 1 and subs[-1].facet is not None and subs[-1].item:
        trailing_facet = subs[-1].facet
        for i in range(len(subs) - 1):
            if subs[i].facet is None and subs[i].item:
                subs[i] = SubRef(momentti=subs[i].momentti, item=subs[i].item, facet=trailing_facet)

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
            subs[i] = SubRef(momentti=last_mom, item=sr.item, facet=sr.facet)
    # Default bare items to momentti=1.
    subs = [
        SubRef(momentti=m if m != 0 or not sr.item else 1, item=sr.item, facet=sr.facet)
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
