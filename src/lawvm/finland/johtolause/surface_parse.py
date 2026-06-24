"""surface_parse — PEG grammar rules and parser for Finnish amendment clauses.

This module owns:
  - Parser combinator primitives (Stream, tok, cat, seq, alt, etc.)
  - SubRef and VerbGroupContext data types
  - All grammar rules (_verb_clause, _target_list, _section_ref, etc.)
  - The core parse() function that produces SurfaceClause directly

Grammar rules emit SurfaceNode types from surface_model.py natively.
The backward-compatibility path to ParsedOp lives in api.py (parse_to_ops).
New callers should use api.parse_clause() instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional, TypeVar, cast

from lawvm.finland.johtolause.lexicon import Token, _RANGE_RE
from lawvm.core.semantic_types import FacetKind
from lawvm.finland.johtolause.surface_model import (
    BackRefArity,
    ScopeKind,
    SurfaceBackRef,
    SurfaceClause as SurfaceClauseModel,
    SurfaceCrossVerbMoveTail,
    SurfaceDescendantCoordination,
    SurfaceHeadingPlacement,
    SurfaceInsertion,
    SurfaceNode,
    SurfaceRelabelFromContext,
    SurfaceRenumberTail,
    SurfaceScopeBlock,
    SurfaceSubRef,
    SurfaceTargetRef,
    SurfaceValiotsikkoRef,
    SurfaceVerbGroup as SurfaceVerbGroupModel,
    SurfaceWitness,
    TargetKind,
    VerbKind,
)
from lawvm.finland.source_verb import SourceVerb


# ---------------------------------------------------------------------------
# Sentinel span categories — imported from sentinels.py (single source of truth).
# ---------------------------------------------------------------------------

from lawvm.finland.johtolause.sentinels import SKIP_CATS as SENTINEL_CATEGORIES

#: Subset of sentinels that appear in reinstatement / citation contexts
#: alongside TILALLE tokens.
_REINST_OR_CITE: frozenset[str] = frozenset(
    {
        "REINST_SPAN",
        "CITATION_SPAN",
    }
)

#: TILALLE + reinstatement/citation sentinels — frequently skipped together
_TILALLE_OR_REINST: frozenset[str] = frozenset(
    {
        "TILALLE",
        "REINST_SPAN",
        "CITATION_SPAN",
    }
)


def _surface_target_kind_for_pair_kind(pair_kind: str) -> TargetKind:
    """Map the pair-kind code from scan annotations to a surface TargetKind."""
    try:
        return TargetKind.from_code(pair_kind)
    except ValueError:
        return TargetKind.SECTION


def _skip_archaic_nain_kuuluva(s: Stream) -> None:
    """Skip archaic ``näin kuuluva`` insert lead-ins, including glued variants."""
    if not (t0 := s.peek()):
        return
    t0_lemma = (t0.lemma or "").lower()
    t0_text = (t0.text or "").lower()
    if t0_lemma == "näin" and (t1 := s.peek(1)) and (t1.lemma or "").lower() in (
        "kuuluva",
        "kuulua",
        "kuluva",
    ):
        s.pos += 2
        return
    if t0_lemma in {"näinkuuluva", "näinkuluva"} or t0_text in {"näinkuuluva", "näinkuluva"}:
        s.pos += 1


def _skip_reinstatement_preamble_to_uusi(s: Stream) -> bool:
    """Skip a bounded reinstatement preamble before ``uusi``.

    Historical sources sometimes put a descriptive provenance phrase between a
    chapter destination and the actual insertion payload:

      ``9 lukuun määräajasta ... kumotun 12 §:n sijaan uusi 12 §``

    The phrase is safe to skip only when it contains a genitive structural
    target followed by ``tilalle``/``sijaan`` (or equivalent reinstatement
    sentinel) before the next ``uusi`` and no verb/end boundary intervenes.
    """
    saved = s.save()
    i = s.pos
    saw_genitive_target = False
    saw_reinstatement = False
    while i < len(s.tokens):
        tok = s.tokens[i]
        if tok.cat in {"VERB", "END", "END_SENTINEL_SPAN"}:
            break
        if tok.cat == "UUSI":
            if saw_genitive_target and saw_reinstatement:
                s.pos = i
                return True
            break
        if tok.cat == "PYKALA" and tok.case == "GEN":
            saw_genitive_target = True
        if tok.cat == "REINST_SPAN":
            saw_genitive_target = True
            saw_reinstatement = True
        if saw_genitive_target and tok.cat in _TILALLE_OR_REINST:
            saw_reinstatement = True
        i += 1
    s.restore(saved)
    return False


# ═══════════════════════════════════════════════════════════════════════
# LAYER 2: Provenance span helper (shared with scan.py annotation layer)
# ═══════════════════════════════════════════════════════════════════════
#
# The legacy strip_* filter functions and apply_filters() pipeline have been
# deleted.  All noise stripping is now handled by the annotation layer in
# scan.py (annotate_* producers + apply_annotations).
#
# _skip_prov_span now lives in the neutral provenance_span module (so the
# construction-grammar package can depend on it without taking a legacy
# surface_parse edge).  It is re-exported here for the in-module call sites.

from lawvm.finland.johtolause.provenance_span import _skip_prov_span


# ═══════════════════════════════════════════════════════════════════════
# LAYER 3: Parser combinator primitives
# ═══════════════════════════════════════════════════════════════════════

T = TypeVar("T")


@dataclass
class Stream:
    """Token stream with cursor and backtracking.

    jolloin_renumber_pairs: Optional mapping from JOLLOIN_MOVE token position
        in the filtered stream to a list of (src_label, dst_label, kind) renumber
        pairs extracted from that jolloin span.  When present, the parser emits
        SurfaceRenumberTail nodes natively instead of discarding jolloin data.
        Populated by parse() when called with jolloin renumber pair data from
        apply_annotations_with_jolloin_pairs().

    consumed_jolloin_positions: Positions of JOLLOIN_MOVE tokens that were
        consumed during parsing and had renumber pairs.  Populated by the
        JOLLOIN_MOVE consumption site in _target_list().  Used by parse() to
        build the prepended SIIRTAA verb group after all verb groups are done.

    consumed_jolloin_contexts: Per-consumed-JOLLOIN anchor context as
        ``(position, last_section, last_section_chapter)``. Moment renumber
        tails need their parent section context to become real section targets
        with ``sub_refs`` instead of disappearing as parser folklore.
    """

    tokens: list[Token]
    pos: int = 0
    jolloin_renumber_pairs: dict[int, list[tuple[str, str, str]]] | None = None
    consumed_jolloin_positions: list[int] | None = None
    consumed_jolloin_contexts: list[tuple[int, str, str]] | None = None

    def at_end(self) -> bool:
        return self.pos >= len(self.tokens)

    def peek(self, offset: int = 0) -> Optional[Token]:
        idx = self.pos + offset
        return self.tokens[idx] if 0 <= idx < len(self.tokens) else None

    def skip_sentinels(self, extra: frozenset[str] | None = None) -> None:
        """Advance past consecutive sentinel span tokens.

        If *extra* is provided, those categories are also skipped.
        """
        cats = SENTINEL_CATEGORIES if extra is None else SENTINEL_CATEGORIES | extra
        while (t := self.peek()) and t.cat in cats:
            self.pos += 1

    def skip_cats(self, cats: frozenset[str]) -> None:
        """Advance past consecutive tokens whose category is in *cats*."""
        while (t := self.peek()) and t.cat in cats:
            self.pos += 1

    def save(self) -> int:
        return self.pos

    def restore(self, pos: int) -> None:
        self.pos = pos


# A Parser is a callable: Stream → Optional[T]
# Returns None on failure (and restores stream position).
# Returns parsed value on success (stream advanced).
Parser = Callable[[Stream], Optional[T]]


def tok(predicate: Callable[[Token], bool]) -> Parser[Token]:
    """Match a single token satisfying predicate."""

    def _parse(s: Stream) -> Optional[Token]:
        t = s.peek()
        if t is not None and predicate(t):
            s.pos += 1
            return t
        return None

    return _parse


def cat(category: str) -> Parser[Token]:
    """Match a token by category."""
    return tok(lambda t: t.cat == category)


def cat_case(category: str, case: str) -> Parser[Token]:
    """Match a token by category and grammatical case."""
    return tok(lambda t: t.cat == category and t.case == case)


def seq(*parsers: Parser[Any]) -> Parser[tuple[Any, ...]]:
    """Match a sequence of parsers. Returns tuple of results."""

    def _parse(s: Stream) -> Optional[tuple[Any, ...]]:
        saved = s.save()
        results: list[Any] = []
        for p in parsers:
            r = p(s)
            if r is None:
                s.restore(saved)
                return None
            results.append(r)
        return tuple(results)

    return _parse


def alt(*parsers: Parser[Any]) -> Parser[Any]:
    """Try each parser in order, return first success."""

    def _parse(s: Stream) -> Any | None:
        for p in parsers:
            r = p(s)
            if r is not None:
                return r
        return None

    return _parse


def opt(parser: Parser[Any], default: Any = None) -> Parser[Any]:
    """Optional: try parser, return default if it fails."""

    def _parse(s: Stream) -> Any:
        r = parser(s)
        return r if r is not None else default

    return _parse


def many(parser: Parser[T]) -> Parser[list[T]]:
    """Zero or more matches."""

    def _parse(s: Stream) -> list[T]:
        results: list[T] = []
        while True:
            r = parser(s)
            if r is None:
                break
            results.append(r)
        return results

    return _parse


def sep_by1(item: Parser[T], sep: Parser[Any]) -> Parser[list[T]]:
    """One or more items separated by sep."""

    def _parse(s: Stream) -> Optional[list[T]]:
        first = item(s)
        if first is None:
            return None
        results = [first]
        while True:
            saved = s.save()
            if sep(s) is None:
                break
            r = item(s)
            if r is None:
                s.restore(saved)
                break
            results.append(r)
        return results

    return _parse


def action(parser: Parser[T], fn: Callable[[T], Any]) -> Parser[Any]:
    """Apply a function to the parser's result."""

    def _parse(s: Stream):
        r = parser(s)
        if r is None:
            return None
        return fn(r)

    return _parse


# ═══════════════════════════════════════════════════════════════════════
# LAYER 4: Finnish amendment grammar — emits SurfaceNode types natively
# ═══════════════════════════════════════════════════════════════════════

# ---- Atomic matchers ----


def _verb(s: Stream) -> Optional[SourceVerb]:
    """Match a VERB token, return verb code (M/K/L/S)."""
    t = s.peek()
    if t is not None and t.cat == "VERB":
        s.pos += 1
        return t.verb_code
    return None


_comma = cat("COMMA")
_conj = cat("CONJ")
_dash = cat("DASH")
_uusi = cat("UUSI")


def _sep(s: Stream) -> Optional[Token]:
    """Match a separator: comma, conjunction, comma+conjunction, or archaic 'a'.

    Archaic 'a' acts as 'ja' in pre-1980s texts:
    "1 §:n 1 momentti a 7 §:n 1 momentti" = "... ja 7 §:n ..."
    Only matched when preceded by structural token and followed by NUM.

    Skips PROVENANCE_SPAN / END_SENTINEL_SPAN tokens (tag-not-delete) that
    may appear between targets and their separators.
    """
    # Skip tag-not-delete span tokens before the separator
    sep_start = s.pos
    s.skip_sentinels()
    skipped_sentinel = s.pos > sep_start
    t = s.peek()
    if t is None:
        return None
    # A provenance/citation span may carry the list separator INSIDE it when the
    # comma that delimits two targets was absorbed into the span's leading edge
    # (anaphoric provenance: "33 §, sellaisena kuin se on ... asetuksessa, 34 §"
    # tags from the first comma through to just before "34 §").  Here the span
    # sits directly between two targets with no surrounding comma, so the
    # ordinary "COMMA" / "CONJ" branches never fire.  When a sentinel was
    # skipped and the next token begins a fresh structural target (NUM -> § /
    # luku / liite ...), the sentinel itself is the separator: return it so the
    # resuming target is not silently dropped.
    if skipped_sentinel and t.cat == "NUM":
        for k in range(s.pos + 1, min(s.pos + 4, len(s.tokens))):
            kt = s.tokens[k]
            if kt.cat in ("PYKALA", "LUKU", "LIITE", "NIMIKE", "MOMENTTI", "KOHTA"):
                # The skipped sentinel is the separator; stream already points at
                # the resuming target's leading NUM.
                return s.tokens[sep_start]
            if kt.cat not in ("NUM", "LETTER", "DASH", "CONJ"):
                break
    if t.cat == "COMMA":
        s.pos += 1
        # A tag-not-delete span (provenance / citation, e.g. "sellaisena kuin
        # se on ... (NNN/YY)") may be flanked by commas between two list items:
        # "N momentti, [CITE], M momentti" / "N momentti, [CITE], ja M momentti".
        # The span qualifies the preceding target and is transparent to list
        # separation.  Absorb the whole "[sentinel] [,] [CONJ]" cluster as a
        # single separator so the following continuation (and the rest of the
        # list) is not dropped.  Guarded on actually having skipped a sentinel
        # so the ordinary "COMMA CONJ" path is unchanged.
        if (sp_t := s.peek()) and sp_t.cat in SENTINEL_CATEGORIES:
            s.skip_sentinels()
            if (t2 := s.peek()) and t2.cat == "COMMA":
                s.pos += 1
        # Optional following conjunction(s). Qualifier stripping can leave
        # duplicated conjunction residue such as "ja sekä" after removing an
        # intermediate alakohta tail; treat that as one structural separator.
        while (t := s.peek()) and t.cat == "CONJ":
            s.pos += 1
        return t
    if t.cat == "CONJ":
        s.pos += 1
        while (t2 := s.peek()) and t2.cat == "CONJ":
            s.pos += 1
        # Stripping an alakohta tail such as "... ja c alakohta," leaves a
        # dangling "CONJ COMMA" before the next list item.  The conjunction
        # coordinated the now-removed alakohta with its parent unit, so the
        # surviving comma is the real separator to the next target.  Absorb
        # it (and any following conjunctions) so the remaining list items are
        # not silently dropped.
        if (t2 := s.peek()) and t2.cat == "COMMA":
            s.pos += 1
            while (t3 := s.peek()) and t3.cat == "CONJ":
                s.pos += 1
            return t2
        return t
    # Archaic 'a' as conjunction
    if (
        t.cat == "LETTER"
        and t.lemma == "a"
        and s.pos > 0
        and s.tokens[s.pos - 1].cat in ("MOMENTTI", "KOHTA", "PYKALA", "LUKU", "OSA", "OTSIKKO", "JOHD", "LIITE")
        and (t1 := s.peek(1)) is not None
        and t1.cat == "NUM"
    ):
        s.pos += 1
        return t
    return None


# ---- Number parsing ----


def _number(s: Stream) -> Optional[str]:
    """Match a NUM token, return its text."""
    t = s.peek()
    if t is not None and t.cat == "NUM":
        s.pos += 1
        return t.text
    return None


def _letter(s: Stream) -> Optional[str]:
    """Match a LETTER token, return its text."""
    t = s.peek()
    if t is not None and t.cat == "LETTER":
        s.pos += 1
        return t.lemma
    return None


def _number_with_suffix(s: Stream) -> Optional[tuple[str, str]]:
    """Match number + optional letter suffix. Returns (num, suffix)."""
    n = _number(s)
    if n is None:
        return None
    suffix = _letter(s) or ""
    return (n, suffix)


def _renumber_number_with_suffix(s: Stream) -> Optional[tuple[tuple[str, str], bool]]:
    """Match renumber target number + optional suffix, tracking translative closure.

    Renumber targets use translative surface forms like ``52:ksi``.  The tokenizer
    normalizes such tokens to ``Token(cat='NUM', case='TRANS', lemma='52')``.
    For renumber bookkeeping we want the normalized number text (``52``), while
    still knowing whether the current element is actually closed by a translative
    marker.
    """
    t = s.peek()
    if t is None or t.cat != "NUM":
        return None
    s.pos += 1
    suffix = _letter(s) or ""
    n = t.lemma if t.case == "TRANS" else t.text
    return (n, suffix), (t.case == "TRANS")


def _expand_range(start: tuple[str, str], end: tuple[str, str]) -> list[tuple[str, str]]:
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


def _number_list(s: Stream) -> Optional[list[tuple[str, str]]]:
    """Parse comma/conj/dash-separated list of numbers with optional suffixes.

    Handles ranges: "21–23" expands to [(21,""), (22,""), (23,"")].
    Handles suffix ranges: "1 a–1 c" expands to [(1,"a"), (1,"b"), (1,"c")].
    """
    first = _number_with_suffix(s)
    if first is None:
        return None
    results = [first]

    while True:
        saved = s.save()
        # Dash = range
        if _dash(s) is not None:
            end = _number_with_suffix(s)
            if end is None:
                # Bare letter after dash: "58 a—h" where end has same base
                let = _letter(s)
                if let and results:
                    end = (results[-1][0], let)
                else:
                    s.restore(saved)
                    break
            expanded = _expand_range(results[-1], end)
            if len(expanded) > 1:
                results.pop()
                results.extend(expanded)
            else:
                results.append(end)
            continue
        # Comma or conj
        if _sep(s) is not None:
            nxt = _number_with_suffix(s)
            if nxt is None:
                s.restore(saved)
                break
            results.append(nxt)
            continue
        break
    return results


def _letter_list(s: Stream) -> Optional[list[str]]:
    """Parse comma/conj/dash-separated list of item letters.

    Handles simple coordinated item arms such as ``a ja h`` as well as
    compact ranges like ``a-c`` when they appear before a trailing
    structural noun:

      ``18 §:n a ja h kohta``
      ``1 momentin a-c kohta``
    """
    first = _letter(s)
    if first is None:
        return None
    results = [first]

    while True:
        saved = s.save()
        if _dash(s) is not None:
            end = _letter(s)
            if end is None:
                s.restore(saved)
                break
            if len(results[-1]) == 1 and len(end) == 1:
                start_ord = ord(results[-1])
                end_ord = ord(end)
                if start_ord <= end_ord and end_ord - start_ord < 26:
                    results.pop()
                    results.extend(chr(c) for c in range(start_ord, end_ord + 1))
                    continue
            results.append(end)
            continue
        if _sep(s) is not None:
            nxt = _letter(s)
            if nxt is None:
                s.restore(saved)
                break
            results.append(nxt)
            continue
        break
    return results


def _renumber_target_list(s: Stream) -> Optional[list[tuple[str, str]]]:
    """Parse translative renumber targets without crossing clause commas.

    Accept only tightly-bound `NUM (CONJ|DASH NUM)*` sequences such as:
    `3:ksi`, `7 ja 8:ksi`, `29–36:ksi`.
    Do not consume comma-separated next section refs.
    """
    saved_start = s.save()
    first_data = _renumber_number_with_suffix(s)
    if first_data is None:
        return None
    first, first_trans = first_data
    results = [first]
    saw_trans = first_trans
    while True:
        saved = s.save()
        t = s.peek()
        if not (t and t.cat in ("CONJ", "DASH")):
            break
        if t.cat == "DASH":
            s.pos += 1
            end_data = _renumber_number_with_suffix(s)
            if end_data is None:
                s.restore(saved)
                break
            end, end_trans = end_data
            if not end_trans:
                s.restore(saved)
                break
            expanded = _expand_range(results[-1], end)
            if len(expanded) > 1:
                results.pop()
                results.extend(expanded)
            else:
                results.append(end)
            saw_trans = True
            continue

        _sep(s)
        more_data = _renumber_number_with_suffix(s)
        if more_data is None:
            s.restore(saved)
            break
        more, more_trans = more_data
        if not more_trans:
            if _dash(s) is None:
                s.restore(saved)
                break
            end_data = _renumber_number_with_suffix(s)
            if end_data is None:
                s.restore(saved)
                break
            end, end_trans = end_data
            if not end_trans:
                s.restore(saved)
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
        s.restore(saved_start)
        return None
    return results


# ---- Sub-reference ----


@dataclass
class SubRef:
    """Parsed sub-reference: momentti, item, or facet.

    This legacy fallback parser still encodes a kohta+alakohta as a single
    ``item`` label (e.g. ``3d``); the canonical separate-level split is applied
    downstream in ``_build_target_address`` / address synthesis.
    """

    momentti: int = 0  # 0 = whole section
    item: str = ""
    facet: Optional[FacetKind] = None


_INTRO_WORDS = frozenset({"johdanto", "johdannon"})


def _is_intro_token(token: Token | None) -> bool:
    if token is None:
        return False
    if token.cat == "JOHD":
        return True
    return token.cat == "WORD" and (token.text or "").lower() in _INTRO_WORDS


def _consume_intro_token(s: Stream) -> bool:
    if _is_intro_token(s.peek()):
        s.pos += 1
        return True
    return False


def _parse_after_gen_kohta(s: Stream) -> Optional[FacetKind]:
    """Check for JOHD after a genitive KOHTA under a momentti context.

    Consumes JOHD if present and returns FacetKind.INTRO.
    Returns None (no token consumed) otherwise.

    Note: OTSIKKO after a genitive kohta is handled separately in the KOHTA
    branch — it is consumed but NOT preserved as a facet (item-level heading
    ops are treated as whole-item ops by the grafter).
    """
    if _consume_intro_token(s):
        return FacetKind.INTRO
    return None


def _parse_descendant_coordination(s: Stream, mom_ctx: int = 0) -> Optional[list[SubRef]]:
    """Recursive descendant-coordination parser for sub-references.

    Handles conjunction at any addressable level.  A trailing qualifier
    (johdantokappale, otsikko) binds to ALL coordinated targets at the same
    level.  Returns a list of ``SubRef`` objects.

    Supported patterns (non-exhaustive):
      ``2 momentti``                              -> [SubRef(2)]
      ``2 ja 3 momentti``                         -> [SubRef(2), SubRef(3)]
      ``2 momentin johdantokappale``              -> [SubRef(2, facet=INTRO)]
      ``2 ja 3 momentin johdantokappale``         -> [SubRef(2, INTRO), SubRef(3, INTRO)]
      ``1 momentin 2 kohta``                      -> [SubRef(1, "2")]
      ``1 momentin 2 ja 3 kohta``                 -> [SubRef(1,"2"), SubRef(1,"3")]
      ``2 ja 3 momentin 1 kohta``                 -> [SubRef(2,"1"), SubRef(3,"1")]
      ``1 kohta``                                 -> [SubRef(0, "1")]
      ``1 kohdan johdantolause``                  -> [SubRef(0, "1", INTRO)]

    Multi-path arms like ``2 momentin 1 kohdan ja 3 momentin 2 kohdan``
    are handled by the outer separator loop in ``_sub_ref``, not here;
    this function parses one coordination group at a time.

    The ``mom_ctx`` parameter carries the current momentti number when
    recursing into a bare KOHTA context.
    """
    saved = s.save()

    nums = _number_list(s)
    if not nums:
        s.restore(saved)
        letters = _letter_list(s)
        t2 = s.peek()
        if letters and t2 and t2.cat == "KOHTA":
            is_kohta_gen = t2.case == "GEN"
            s.pos += 1

            if is_kohta_gen:
                if _consume_intro_token(s):
                    return [SubRef(mom_ctx, let, facet=FacetKind.INTRO) for let in letters]
                t3 = s.peek()
                if t3 and t3.cat == "OTSIKKO":
                    s.pos += 1
                return [SubRef(mom_ctx, let) for let in letters]

            return [SubRef(momentti=mom_ctx, item=let) for let in letters]
        return None

    t2 = s.peek()

    # ── MOMENTTI branch ──────────────────────────────────────────────────────
    if t2 and t2.cat == "MOMENTTI":
        is_gen = t2.case == "GEN"
        s.pos += 1  # consume MOMENTTI

        if is_gen:
            # Compute integer momentti values for all nums in the list.
            mom_vals: list[int] = []
            for n, _sf in nums:
                for rn in _expand_range_single(n):
                    mom_vals.append(int(rn) if rn.isdigit() else 0)

            # Try kohta descent: "N momentin K kohta" / "N ja M momentin K kohta"
            saved_kohta = s.save()
            knums = _number_list(s)
            if knums and (t := s.peek()) and t.cat == "KOHTA":
                is_kohta_gen2 = t.case == "GEN"
                s.pos += 1
                if is_kohta_gen2:
                    # "N momentin K kohdan johdantolause"
                    facet = _parse_after_gen_kohta(s)
                    return [
                        SubRef(mom, kn + ksf, facet=facet)
                        for mom in mom_vals
                        for kn, ksf in knums
                    ]
                # "N momentin K kohta" (nominative)
                return [SubRef(mom, kn + ksf) for mom in mom_vals for kn, ksf in knums]
            s.restore(saved_kohta)

            # Try letter-KOHTA: "N momentin a-kohta"
            saved_lk = s.save()
            letters = _letter_list(s)
            if letters and (t := s.peek()) and t.cat == "KOHTA":
                s.pos += 1
                return [SubRef(mom, let) for mom in mom_vals for let in letters]
            if letters:
                s.restore(saved_lk)

            # Try trailing qualifier shared across all momenti:
            # "N ja M momentin johdantokappale" -> all get INTRO facet
            t3 = s.peek()
            if _consume_intro_token(s):
                return [SubRef(mom, facet=FacetKind.INTRO) for mom in mom_vals]
            if t3 and t3.cat == "OTSIKKO":
                s.pos += 1
                return [SubRef(mom, facet=FacetKind.HEADING) for mom in mom_vals]

            # No sub-structure: plain momentti refs.
            return [SubRef(mom) for mom in mom_vals]

        # Nominative MOMENTTI: "N momentti" or "N ja M momentti"
        result: list[SubRef] = []
        for n, _sf in nums:
            for rn in _expand_range_single(n):
                result.append(SubRef(momentti=int(rn) if rn.isdigit() else 0))
        return result

    # ── KOHTA branch ─────────────────────────────────────────────────────────
    if t2 and t2.cat == "KOHTA":
        is_kohta_gen = t2.case == "GEN"
        s.pos += 1

        if is_kohta_gen:
            # JOHD after genitive kohta: "N kohdan johdantolause" -> preserve facet
            # OTSIKKO: consumed but NOT preserved as a facet — item-level heading
            # ops are treated as whole-item ops by the grafter.
            t3 = s.peek()
            if _consume_intro_token(s):
                return [SubRef(mom_ctx, n + sf, facet=FacetKind.INTRO) for n, sf in nums]
            if t3 and t3.cat == "OTSIKKO":
                s.pos += 1  # consume but do not set facet
            return [SubRef(mom_ctx, n + sf) for n, sf in nums]

        # Nominative KOHTA
        return [SubRef(momentti=mom_ctx, item=n + sf) for n, sf in nums]

    # Numbers without structural noun: not a valid sub-ref.
    s.restore(saved)
    return None


def _sub_ref(s: Stream) -> Optional[list[SubRef]]:
    """Parse sub-reference after § token.

    Returns list because conjunctive lists produce multiple sub-refs:
    "1 ja 2 momentti" -> [SubRef(1), SubRef(2)]
    "2 ja 3 momentin johdantokappale" -> [SubRef(2, INTRO), SubRef(3, INTRO)]
    "2 ja 3 momentin 1 kohta" -> [SubRef(2, "1"), SubRef(3, "1")]

    Delegates the recursive descent logic to
    ``_parse_descendant_coordination()``.
    """
    saved = s.save()

    # Special: otsikko / johdantokappale at section level (no momentti prefix)
    t = s.peek()
    if t and t.cat == "OTSIKKO":
        s.pos += 1
        return [SubRef(facet=FacetKind.HEADING)]
    if _consume_intro_token(s):
        return [SubRef(facet=FacetKind.INTRO)]

    # "edellä oleva (väli)otsikko" — heading-CHANGE reference (locative
    # "edellä" + participle of "olla"), distinct from the heading-INSERT form
    # "N §:n edelle uusi väliotsikko" (allative "edelle" + "uusi") guarded in
    # _section_ref. The change form binds the preceding section number as a
    # heading-amend target so the enclosing kumotaan/muutetaan list keeps going.
    if t and t.cat == "EDELLA" and (t.text or "").lower() == "edellä":
        saved_e = s.save()
        s.pos += 1
        if (t := s.peek()) and t.lemma == "olla":
            # "edellä oleva [luvun] otsikko" — optional LUKU genitive
            # ("luvun otsikko" = the chapter heading preceding the section).
            s.pos += 1
            if (t := s.peek()) and t.cat == "LUKU":
                s.pos += 1
            if (t := s.peek()) and t.cat == "OTSIKKO":
                s.pos += 1
                return [SubRef(facet=FacetKind.HEADING)]
        elif (t := s.peek()) and (t.text or "").lower() == "olevien":
            # Plural participle: "edellä olevien lukujen otsikoiden numerointi"
            # — a heading-renumbering reference distributed over the preceding
            # section list. Consume the descriptive tail up to "numerointi".
            tail_saved = s.save()
            s.pos += 1
            saw_otsikko = False
            while (t := s.peek()) and t.cat in ("LUKU", "OTSIKKO", "WORD"):
                if t.cat == "OTSIKKO" or (t.text or "").lower().startswith("otsiko"):
                    saw_otsikko = True
                consumed_numerointi = (t.text or "").lower().startswith("numeroin")
                s.pos += 1
                if consumed_numerointi:
                    break
            if saw_otsikko:
                return [SubRef(facet=FacetKind.HEADING)]
            s.restore(tail_saved)
        s.restore(saved_e)

    # Recursive descent coordination: handles momentti and kohta at all depths.
    result = _parse_descendant_coordination(s)
    if result is not None:
        return result

    # Letter + KOHTA: "H kohta" (no number prefix)
    let = _letter(s)
    if let:
        if (t := s.peek()) and t.cat == "KOHTA":
            s.pos += 1
            return [SubRef(momentti=0, item=let)]
        s.restore(saved)
        return None

    return None


def _expand_range_single(n: str) -> list[str]:
    """If n looks like '21–23', expand; otherwise return [n]."""
    m = _RANGE_RE.match(n)
    if m:
        start, end = int(m.group(1)), int(m.group(2))
        if end > start and end - start < 50:
            return [str(i) for i in range(start, end + 1)]
    return [n]


# ---- Helper: convert SubRef list to SurfaceSubRef tuple ----


def _to_surface_sub_refs(subs: list[SubRef]) -> tuple[SurfaceSubRef, ...]:
    """Convert parser SubRef list to tuple of SurfaceSubRef."""
    result = []
    for sr in subs:
        # Map facet to legacy special field for backward compat
        special = ""
        if sr.facet == FacetKind.HEADING:
            special = "otsikko"
        elif sr.facet == FacetKind.INTRO:
            special = "johd"
        result.append(
            SurfaceSubRef(
                momentti=sr.momentti,
                item=sr.item,
                facet=sr.facet,
                special=special,
            )
        )
    return tuple(result)


def _make_witness(rule_id: str, start: int, end: int) -> SurfaceWitness:
    """Create a SurfaceWitness from rule_id and span."""
    return SurfaceWitness(rule_id=rule_id, source_span=(start, end))


# ---- Target rules — emit SurfaceNode types ----


def _is_whole_target(node: SurfaceTargetRef) -> bool:
    """Check if a SurfaceTargetRef represents a whole-target (no sub-level targeting)."""
    if not node.sub_refs:
        return True
    # A single empty SubRef also means whole-target
    return node.sub_refs == (SurfaceSubRef(),)


def _is_heading_only_target(node: SurfaceTargetRef) -> bool:
    """Check if a target amends only the target heading facet."""
    return bool(node.sub_refs) and all(
        sr.facet == FacetKind.HEADING and sr.momentti == 0 and not sr.item
        for sr in node.sub_refs
    )


def _part_ctx(s: Stream) -> Optional[str]:
    """Parse 'N osan' — part context in genitive."""
    saved = s.save()
    n = _number(s)
    if n is None:
        return None
    if (t := s.peek()) and t.cat == "OSA" and t.case == "GEN":
        s.pos += 1
        return n
    s.restore(saved)
    return None


def _chapter_ctx(s: Stream) -> Optional[str]:
    """Parse 'N luvun' — chapter context in genitive."""
    saved = s.save()
    n = _number(s)
    if n is None:
        return None
    sf = _letter(s) or ""
    if (t := s.peek()) and t.cat == "LUKU" and t.case == "GEN":
        s.pos += 1
        return n + sf
    s.restore(saved)
    return None


def _parse_backref_continuation(s: Stream) -> Optional[list[SubRef]]:
    """Parse backref continuation: [sep] BACKREF PYKALA sub_refs.

    Patterns:
      "ja mainitun pykälän 1 momentti"     → singular, sub-refs for one section
      "ja mainittu pykälä"                  → singular, whole section
      "ja mainittujen pykälien otsikot"     → plural, sub-refs for all sections
      ", mainitun pykälän otsikko ja 1 momentti" → comma-led, multiple sub-refs

    Returns list of SubRef if backref found, None otherwise.
    Consumes tokens on success, restores on failure.
    """
    saved = s.save()
    # Optional separator (ja / , / , ja / sekä)
    _sep(s)
    t = s.peek()
    if not (t and t.cat == "BACKREF"):
        s.restore(saved)
        return None
    s.pos += 1  # consume BACKREF
    pyk = s.peek()
    if not (pyk and pyk.cat == "PYKALA"):
        s.restore(saved)
        return None
    s.pos += 1  # consume pykälän/pykälä/pykälien/pykälät

    # Parse sub-refs after "pykälän"
    subs = _sub_ref(s)
    # Additional sub-refs joined by separators
    while subs:
        saved2 = s.save()
        if _sep(s) is None:
            break
        more = _sub_ref(s)
        if more:
            subs.extend(more)
        else:
            s.restore(saved2)
            break
    if not subs:
        subs = [SubRef()]  # whole section ("mainittu pykälä")
    # Trailing facet distribution (same logic as in _section_ref):
    # distribute kohta-level trailing facet to preceding same-depth arms.
    if len(subs) > 1 and subs[-1].facet is not None and subs[-1].item:
        trailing_facet = subs[-1].facet
        for i in range(len(subs) - 1):
            if subs[i].facet is None and subs[i].item:
                subs[i] = SubRef(
                    momentti=subs[i].momentti,
                    item=subs[i].item,
                    facet=trailing_facet,
                )
    return subs


def _parse_part_backref_target(
    s: Stream,
    verb: SourceVerb,
    chapter: str,
    part: str,
) -> Optional[list[SurfaceNode]]:
    """Parse ``[sep] mainitun osan ...`` as a fresh target batch.

    After a whole-part target, clauses like ``mainitun osan 1 luvun 1 §:n
    numero 136:ksi`` introduce new scoped section targets under the same source
    part. They are not subsection back-references and must not be silently
    consumed as residue.
    """
    if not part:
        return None
    saved = s.save()
    _sep(s)
    t = s.peek()
    if not (t and t.cat == "BACKREF"):
        s.restore(saved)
        return None
    s.pos += 1
    t = s.peek()
    if not (t and t.cat == "OSA"):
        s.restore(saved)
        return None
    s.pos += 1
    nodes = _section_ref(s, verb, chapter, part=part) or _chapter_ref(s, verb, part=part)
    if nodes:
        return nodes
    s.restore(saved)
    return None


def _parse_chapter_backref_target(
    s: Stream,
    verb: SourceVerb,
    chapter: str,
    part: str,
) -> Optional[list[SurfaceNode]]:
    """Parse ``[sep] mainitun luvun ...`` as a fresh target batch."""
    if not chapter:
        return None
    saved = s.save()
    _sep(s)
    t = s.peek()
    if not (t and t.cat == "BACKREF"):
        s.restore(saved)
        return None
    s.pos += 1
    t = s.peek()
    if not (t and t.cat == "LUKU"):
        s.restore(saved)
        return None
    s.pos += 1
    nodes = _section_ref(s, verb, chapter, part=part)
    if nodes:
        return nodes
    s.restore(saved)
    return None


def _maybe_wrap_scope_block(
    nodes: list[SurfaceNode],
    scope_ch: str,
    scope_pt: str,
    span_start: int,
    span_end: int,
) -> list[SurfaceNode]:
    """Wrap nodes in a SurfaceScopeBlock if an explicit scope was parsed.

    Conditions for wrapping:
      - At least one of scope_ch or scope_pt is set.
      - All nodes are SurfaceTargetRef (SurfaceScopeBlock.targets is typed as
        Tuple[SurfaceTargetRef, ...]).

    When both scope_ch and scope_pt are set (part+chapter both explicit), a
    single part-level scope block is emitted.  The caller must leave chapter on
    the individual targets so the resolver can recover it; only part is cleared
    from targets in that case.

    When only one scope is set, the caller has already cleared the corresponding
    field from individual targets.
    """
    if not scope_ch and not scope_pt:
        return nodes
    if not nodes:
        return nodes
    # Only wrap if all nodes are plain SurfaceTargetRef — scope block targets
    # field is typed Tuple[SurfaceTargetRef, ...].
    if not all(isinstance(n, SurfaceTargetRef) for n in nodes):
        return nodes
    target_nodes = cast(list[SurfaceTargetRef], nodes)
    # When both are set, prefer part as outer scope (chapter stays on targets).
    scope_kind = ScopeKind.PART if scope_pt else ScopeKind.CHAPTER
    scope_label = scope_pt if scope_pt else scope_ch
    _w = _make_witness(f"fi.scope_block_{scope_kind.value}", span_start, span_end)
    return [
        SurfaceScopeBlock(
            scope_kind=scope_kind,
            scope_label=scope_label,
            targets=tuple(target_nodes),
            witness=_w,
        )
    ]


def _section_ref(s: Stream, verb: SourceVerb, chapter: str, part: str = "") -> Optional[list[SurfaceNode]]:
    """Parse section reference(s): [part_ctx] [chapter_ctx] number_list § sub_ref?

    Returns list of SurfaceTargetRef nodes, or a single SurfaceScopeBlock when
    an explicit chapter or part prefix is parsed in this span.
    """
    saved = s.save()
    # Track explicitly parsed context vs inherited context separately so we know
    # whether to emit a SurfaceScopeBlock.
    explicit_pt = _part_ctx(s)
    pt = explicit_pt if explicit_pt is not None else part
    explicit_ch = _chapter_ctx(s)
    ch = explicit_ch if explicit_ch is not None else chapter
    nums = _number_list(s)
    if not nums:
        s.restore(saved)
        return None
    t = s.peek()
    if not (t and t.cat == "PYKALA" and t.case != "ILL"):
        s.restore(saved)
        return None
    pyk_pos = s.pos  # save § position for separator scan
    s.pos += 1  # consume §

    # Handle renumbering: "§:n numero N:ksi [ja mainitun/mainittujen pykälän/pykälien sub_refs]"
    if (t := s.peek()) and t.cat == "NUMERO":
        s.pos += 1  # consume "numero"
        renumber_targets = _renumber_target_list(s) or []
        source_labels: list[str] = []
        for n, sf in nums:
            expanded = _expand_range_single(n)
            if len(expanded) == 1:
                source_labels.append(expanded[0] + sf)
            else:
                source_labels.extend(expanded)
        destination_labels: list[str] = []
        for n, sf in renumber_targets:
            expanded = _expand_range_single(n)
            if len(expanded) == 1:
                destination_labels.append((expanded[0] + sf).removesuffix(":ksi"))
            else:
                destination_labels.extend(label.removesuffix(":ksi") for label in expanded)
        destination_by_source = (
            dict(zip(source_labels, destination_labels, strict=True)) if len(source_labels) == len(destination_labels) else {}
        )
        # Whole-section nodes for the renumbered section(s).
        # When emitting a scope block (explicit chapter/part), the scope block
        # provides the outer context; targets omit the field the scope covers.
        # When BOTH are explicit: part scope block is outer; chapter stays on
        # individual targets (resolver applies part from block, chapter from target).
        scope_ch = ch if explicit_ch is not None and explicit_pt is None else ""
        scope_pt = pt if explicit_pt is not None else ""
        # Part cleared from targets when part scope block is emitted.
        # Chapter cleared from targets only when chapter-only scope block is emitted.
        target_ch = "" if (scope_ch and not scope_pt) else ch
        target_pt = "" if scope_pt else pt
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
                        witness=_make_witness("fi.section_renumber", saved, s.pos),
                    )
                )
        # Check for backref continuation: ", mainitun pykälän ..." or "ja mainitun pykälän ..."
        backref_subs = _parse_backref_continuation(s)
        if backref_subs:
            _w = _make_witness("fi.section_renumber", saved, s.pos)
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
                            sub_refs=_to_surface_sub_refs(backref_subs),
                            notes=("renumber_clause", "renumber_backref_clause"),
                            renumber_dest=dest_label,
                            witness=_w,
                        )
                    )
        return _maybe_wrap_scope_block(nodes, scope_ch, scope_pt, saved, s.pos)

    subs = _sub_ref(s)
    if not subs:
        # Guard: "N §:n edelle uusi [luvun] otsikko" is a heading-placement
        # phrase, not a section reference.
        if (t := s.peek()) and t.cat == "EDELLA":
            s.restore(saved)
            return None
        subs = [SubRef()]  # whole section

    # Additional same-section sub-refs joined by separators
    while True:
        saved2 = s.save()
        if _sep(s) is None:
            break
        more = _sub_ref(s)
        if more:
            subs.extend(more)
        else:
            s.restore(saved2)
            break

    # Trailing facet distribution: when the last sub-ref has a facet
    # (INTRO/HEADING) and an item (kohta level), distribute that facet to
    # preceding sub-refs that also have items but no facet.  This handles
    # the Finnish coordination pattern where a trailing qualifier distributes
    # over conjoined genitive kohta arms:
    #   "2 momentin 1 kohdan ja 3 momentin 2 kohdan johdantolause"
    #   -> both arms get INTRO, not just the last one.
    # Only distribute at kohta level (item is non-empty) to avoid
    # cross-depth contamination.  Momentti-level facet distribution
    # like "N ja M momentin johdantokappale" is already handled within
    # _parse_descendant_coordination() itself.
    if len(subs) > 1 and subs[-1].facet is not None and subs[-1].item:
        trailing_facet = subs[-1].facet
        for i in range(len(subs) - 1):
            if subs[i].facet is None and subs[i].item:
                subs[i] = SubRef(
                    momentti=subs[i].momentti,
                    item=subs[i].item,
                    facet=trailing_facet,
                )

    # Trailing sub-ref scoping: when multiple section numbers precede § with
    # sub-refs, scope distribution depends on the last separator before §.
    leading_whole: list[tuple[str, str]] = []
    scoped_nums = nums
    if subs and subs != [SubRef()] and len(nums) > 1:
        _parallel_pair = False
        if len(nums) == 2:
            for _scan_idx in range(pyk_pos - 1, -1, -1):
                _tk = s.tokens[_scan_idx]
                if _tk.cat in ("CONJ", "SEKA"):
                    _parallel_pair = True
                    break
                if _tk.cat == "COMMA":
                    break
                if _tk.cat in ("NUM", "LETTER"):
                    continue
                break
        if not _parallel_pair:
            leading_whole = nums[:-1]
            scoped_nums = [nums[-1]]

    # Propagate momentti context to bare kohta items
    last_mom = 0
    for i, sr in enumerate(subs):
        if sr.momentti != 0:
            last_mom = sr.momentti
        elif sr.item and last_mom != 0:
            subs[i] = SubRef(momentti=last_mom, item=sr.item, facet=sr.facet)
    # Default bare items to momentti=1
    subs = [
        SubRef(momentti=m if m != 0 or not sr.item else 1, item=sr.item, facet=sr.facet)
        for sr in subs
        for m in [sr.momentti]
    ]

    # Determine scope block eligibility.
    # A scope block wraps SurfaceTargetRef nodes only (type constraint on targets field).
    # Emit a scope block whenever an explicit chapter or part was parsed.
    # When BOTH are explicit: part is the outer scope block; chapter is preserved
    # on individual targets so the resolver can recover it.
    # When explicit scope is present AND >=2 sub-refs would normally produce a
    # SurfaceDescendantCoordination: emit SurfaceTargetRef with sub_refs instead so the
    # scope block can wrap it. Downstream lowering produces the same RefAmend expansion.
    scope_ch = ch if explicit_ch is not None and explicit_pt is None else ""
    scope_pt = pt if explicit_pt is not None else ""
    has_explicit_scope = bool(scope_ch or scope_pt)
    # Part cleared from targets when part scope block is emitted.
    # Chapter cleared from targets only when chapter-only scope block is emitted.
    target_ch = "" if (scope_ch and not scope_pt) else ch
    target_pt = "" if scope_pt else pt

    # Build surface nodes
    _w = _make_witness("fi.section_ref", saved, s.pos)
    nodes = []
    # Leading sections get whole-section nodes
    for n, sf in leading_whole:
        for rn in _expand_range_single(n):
            full = rn + (sf if len(_expand_range_single(n)) == 1 else "")
            nodes.append(
                SurfaceTargetRef(
                    kind=TargetKind.SECTION,
                    label=full,
                    chapter=target_ch,
                    part=target_pt,
                    witness=_w,
                )
            )
    # Scoped sections get sub-ref nodes.
    # With >=2 sub-refs and no explicit scope: emit SurfaceDescendantCoordination.
    # With >=2 sub-refs and explicit scope: emit SurfaceTargetRef with sub_refs so the
    # scope block can wrap it (SurfaceScopeBlock.targets must be SurfaceTargetRef).
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
                    witness=_w,
                )
                nodes.append(
                    SurfaceDescendantCoordination(
                        base=base,
                        arms=surface_subs,
                        witness=_w,
                    )
                )
            else:
                nodes.append(
                    SurfaceTargetRef(
                        kind=TargetKind.SECTION,
                        label=full,
                        chapter=target_ch,
                        part=target_pt,
                        sub_refs=surface_subs,
                        witness=_w,
                    )
                )
    return _maybe_wrap_scope_block(nodes, scope_ch, scope_pt, saved, s.pos)


def _pykala_prefix_section_ref(
    s: Stream, verb: SourceVerb, chapter: str, part: str = ""
) -> Optional[list[SurfaceNode]]:
    """Parse the genitive-plural prefix list form: ``pykälien <numlist>``.

    The suffix form places the structural noun after each number
    (``5 §:n`` / ``5 ja 6 §``).  The prefix/postfix-genitive form puts a
    genitive-plural ``pykälien`` BEFORE the number list:

      ``pykälien 1, 9, 45, 57, 66, 93 a, 95 ja 99 suomenkielinen sanamuoto``

    (the language qualifier is stripped by the annotation layer, leaving a
    bare ``PYKALA(gen) NUM ...`` run).  Each listed number is a whole-section
    target.

    Only the genitive-plural ``pykälien`` lemma triggers this form — the
    genitive-singular ``pykälän`` is a back-reference / descendant anchor and
    is handled elsewhere.  If a structural noun follows the list, the last
    number belongs to a suffix-form reference, not to this prefix list.
    """
    saved = s.save()
    t = s.peek()
    if not (t and t.cat == "PYKALA" and t.case == "GEN"):
        return None
    if (t.text or "").lower() != "pykälien":
        return None
    s.pos += 1
    nums = _number_list(s)
    if not nums:
        s.restore(saved)
        return None
    # If a structural noun follows the list, the last number belongs to a
    # suffix-form reference (e.g. "... 95 ja 99 §"), not to this prefix list.
    if (nxt := s.peek()) and nxt.cat in ("PYKALA", "MOMENTTI", "KOHTA", "LUKU", "OSA"):
        s.restore(saved)
        return None
    _w = _make_witness("fi.section_ref_pykala_prefix", saved, s.pos)
    nodes: list[SurfaceNode] = []
    for n, sf in nums:
        for rn in _expand_range_single(n):
            full = rn + (sf if len(_expand_range_single(n)) == 1 else "")
            nodes.append(
                SurfaceTargetRef(
                    kind=TargetKind.SECTION,
                    label=full,
                    chapter=chapter,
                    part=part,
                    witness=_w,
                )
            )
    return nodes


def _chapter_ref(s: Stream, verb: SourceVerb, part: str = "") -> Optional[list[SurfaceNode]]:
    """Parse chapter reference(s): [part_ctx] number_list LUKU [otsikko] [numero N:ksi]."""
    saved = s.save()
    pt = _part_ctx(s) or part
    nums = _number_list(s)
    reversed_order = False
    if nums:
        t = s.peek()
        if not (t and t.cat == "LUKU" and t.case != "ILL"):
            s.restore(saved)
            nums = None
    if nums is None:
        s.restore(saved)
        pt = _part_ctx(s) or part
        t = s.peek()
        if not (t and t.cat == "LUKU" and t.case != "ILL"):
            s.restore(saved)
            return None
        s.pos += 1
        first = _number_with_suffix(s)
        if first is None:
            s.restore(saved)
            return None
        # Guard against descendant continuations like "luvun 9 §:ään uusi ...".
        # In that shape bare genitive "luvun" carries forward the inherited
        # chapter context and the following number belongs to a section target,
        # not a reversed chapter reference.
        t_after = s.peek()
        if t_after and t_after.cat == "PYKALA":
            s.restore(saved)
            return None
        nums = [first]
        reversed_order = True
    else:
        s.pos += 1
    chapter_facet: Optional[FacetKind] = None
    if (t := s.peek()) and t.cat == "OTSIKKO":
        s.pos += 1
        chapter_facet = FacetKind.HEADING
    elif (t := s.peek()) and t.cat == "JOHD":
        s.pos += 1
        chapter_facet = FacetKind.INTRO
    if (
        chapter_facet is not None
        and (t := s.peek())
        and t.cat in {"JA", "CONJ"}
        and (nxt := s.peek(1))
        and nxt.cat == "NUMERO"
    ):
        s.pos += 1
    # Handle chapter-level renumbering: "luvun numero N:ksi"
    destination_by_source: dict[str, str] = {}
    has_renumber = False
    if (t := s.peek()) and t.cat == "NUMERO":
        has_renumber = True
        s.pos += 1  # consume "numero"
        renumber_targets = _renumber_target_list(s) or []
        source_labels = [n + sf for n, sf in nums]
        destination_labels = [(n + sf).removesuffix(":ksi") for n, sf in renumber_targets]
        destination_by_source = (
            dict(zip(source_labels, destination_labels, strict=True)) if len(source_labels) == len(destination_labels) else {}
        )
    if has_renumber:
        _rid = "fi.chapter_renumber"
    elif reversed_order:
        _rid = "fi.chapter_ref_reversed"
    else:
        _rid = "fi.chapter_ref"
    _w = _make_witness(_rid, saved, s.pos)
    nodes: list[SurfaceNode] = []
    for n, sf in nums:
        for rn in _expand_range_single(n):
            notes_list: list[str] = []
            if has_renumber:
                notes_list.append("renumber_clause")
            dest_label = destination_by_source.get(rn + sf, "")
            if dest_label:
                notes_list.append(f"renumber_destination={dest_label}")
            sub_refs: tuple[SurfaceSubRef, ...] = ()
            if chapter_facet:
                sub_refs = (SurfaceSubRef(facet=chapter_facet),)
            nodes.append(
                SurfaceTargetRef(
                    kind=TargetKind.CHAPTER,
                    label=rn + sf,
                    chapter="",
                    part=pt,
                    sub_refs=sub_refs,
                    notes=tuple(notes_list),
                    renumber_dest=dest_label,
                    witness=_w,
                )
            )
    return nodes


def _part_ref(s: Stream, verb: SourceVerb, chapter: str = "") -> Optional[list[SurfaceNode]]:
    """Parse part reference(s): number_list OSA [otsikko | chapter_ref | section_ref]."""
    saved = s.save()
    nums = _number_list(s)
    if not nums:
        return None
    if not ((t := s.peek()) and t.cat == "OSA"):
        s.restore(saved)
        return None
    osa_case = t.case
    s.pos += 1

    # Shared heading qualifier over coordinated containers:
    # ``II osan ja 5 luvun otsikko`` targets both the part heading and the
    # chapter heading.  It must not first become a whole-part target, because
    # that would also leak ``II`` as inherited part scope to later explicit
    # chapter targets in the same target list.
    if osa_case == "GEN" and len(nums) == 1:
        pt = nums[0][0] + nums[0][1]
        saved_coord = s.save()
        if _sep(s) is not None:
            ch_nodes = _chapter_ref(s, verb, part=pt)
            if ch_nodes and all(
                isinstance(node, SurfaceTargetRef)
                and node.kind == TargetKind.CHAPTER
                and _is_heading_only_target(node)
                for node in ch_nodes
            ):
                part_heading = SurfaceTargetRef(
                    kind=TargetKind.PART,
                    label=pt,
                    sub_refs=(SurfaceSubRef(facet=FacetKind.HEADING),),
                    witness=_make_witness("fi.coordinated_part_chapter_heading_ref", saved, s.pos),
                )
                return [part_heading, *ch_nodes]
        s.restore(saved_coord)

    # Part as context prefix: "II osan 1 luvun ..." or "II osan 1 §"
    if osa_case == "GEN" and len(nums) == 1:
        pt = nums[0][0] + nums[0][1]
        ch_nodes = _chapter_ref(s, verb, part=pt)
        if ch_nodes:
            return ch_nodes
        sec_nodes = _section_ref(s, verb, chapter, part=pt)
        if sec_nodes:
            return sec_nodes

    # Whole-part target: "II osa" or "II osan otsikko"
    part_facet: Optional[FacetKind] = None
    if (t := s.peek()) and t.cat == "OTSIKKO":
        s.pos += 1
        part_facet = FacetKind.HEADING
    # Handle part-level renumbering
    destination_by_source: dict[str, str] = {}
    has_renumber = False
    if (t := s.peek()) and t.cat == "NUMERO":
        has_renumber = True
        s.pos += 1
        renumber_targets = _renumber_target_list(s) or []
        source_labels = [n + sf for n, sf in nums]
        destination_labels = [(n + sf).removesuffix(":ksi") for n, sf in renumber_targets]
        destination_by_source = (
            dict(zip(source_labels, destination_labels, strict=True)) if len(source_labels) == len(destination_labels) else {}
        )
    _rid = "fi.part_renumber" if has_renumber else "fi.part_ref"
    _w = _make_witness(_rid, saved, s.pos)
    nodes: list[SurfaceNode] = []
    for n, sf in nums:
        notes_list: list[str] = []
        if has_renumber:
            notes_list.append("renumber_clause")
        dest_label = destination_by_source.get(n + sf, "")
        if dest_label:
            notes_list.append(f"renumber_destination={dest_label}")
        sub_refs: tuple[SurfaceSubRef, ...] = ()
        if part_facet:
            sub_refs = (SurfaceSubRef(facet=part_facet),)
        nodes.append(
            SurfaceTargetRef(
                kind=TargetKind.PART,
                label=n + sf,
                chapter="",
                part="",
                sub_refs=sub_refs,
                notes=tuple(notes_list),
                renumber_dest=dest_label,
                witness=_w,
            )
        )
    return nodes


def _nimike_ref(s: Stream, verb: SourceVerb) -> Optional[list[SurfaceNode]]:
    """Parse nimike (title) reference."""
    saved = s.save()
    _number_list(s)  # optional leading number (rare)
    # Skip optional doc-type genitive
    if (t := s.peek()) and t.cat == "DOC" and t.case == "GEN":
        s.pos += 1
    if not ((t := s.peek()) and t.cat == "NIMIKE"):
        s.restore(saved)
        return None
    s.pos += 1
    _w = _make_witness("fi.nimike_ref", saved, s.pos)
    return [
        SurfaceTargetRef(
            kind=TargetKind.NIMIKE,
            label="",
            witness=_w,
        )
    ]


# Appendix part-selector tail tuning. After a matched ``LIITE``, an appendix
# sub-part edit selects a sub-component of the annex with a ``<ROMAN/LETTER/NUM>
# osa[n]`` part-selector (``liitteenä olevan maksutaulukon I osan ... ja III osan
# ...``, ``liitteen 1 I osan ...``) and/or a coordinated sibling appendix
# (``1 liitteen 4 kohta ja 2 liitteen 6 kohta``). The base ``_appendix_ref``
# consumed only the leading ``liite`` token, dropping every following part / sibling
# selector. The tail consumer below covers them so they are not silently dropped.
#
# It is a measure-faithful coverage extension, not a full appendix sub-structure
# model: it emits one ``APPENDIX`` target per part-selector / sibling so the OSA /
# LIITE label tokens are owned, and absorbs the intervening descriptive content
# (``maksutaulukon``, ``alusliikennettä koskeva kohta``) the appendix edit carries.
# It only ENTERS the tail when a part-selector or coordinated sibling actually
# follows (guarded), and STOPS at any clause boundary / fresh operative opener
# (VERB / provenance / insertion / section / chapter), so a plain whole-appendix
# ref and an unrelated trailing target are untouched.
#
# Descriptive / structural cats that may sit INSIDE the appendix sub-edit tail
# (between or after part-selectors) without ending it. KOHTA / OTSIKKO / MOMENTTI /
# ALAKOHTA here are appendix sub-items (``I osan ... kohta``), NOT separate
# operative targets — in these clauses they are dropped by the base parser, so
# absorbing them removes no produced op.
_APPENDIX_TAIL_SKIP_CATS: frozenset[str] = frozenset(
    {
        "WORD", "NUM", "LETTER", "KOHTA", "OTSIKKO", "MOMENTTI", "ALAKOHTA",
        "PUNCT", "CITATION_SPAN", "COMMA", "CONJ", "SEKA", "DASH",
    }
)
# Hard boundaries: a fresh operative opener or a clause boundary ends the appendix
# sub-edit tail (the following content is a separate target the section / insertion
# families own, or the clause end).
_APPENDIX_TAIL_BOUNDARY_CATS: frozenset[str] = frozenset(
    {
        "VERB", "UUSI", "TILALLE", "PYKALA", "LUKU", "NIMIKE",
        "END_SENTINEL_SPAN", "PROVENANCE_SPAN", "PROV", "REINST_SPAN", "DOC",
    }
)
_APPENDIX_PART_ENTRY_WINDOW = 6


def _appendix_part_selector(s: Stream) -> Optional[tuple[str, str]]:
    """At the cursor, match an appendix ``osa`` part-selector.

    Two label placements:
      * ``<num/roman> osa[n]`` — the part number precedes the noun (``I osan``);
      * ``osa[n] <letter>`` — the part letter follows the noun (``osa A``), where
        the appendix's own number was already consumed as the ``liite`` label.

    Returns the (number, suffix) label of the part, advancing past the OSA token
    (and any post-noun letter), or None (no advance) when not on a part-selector.
    """
    saved = s.save()
    num = _number(s)
    if num is not None:
        suffix = _letter(s) or ""
        if (t := s.peek()) and t.cat == "OSA":
            s.pos += 1
            return (num, suffix)
        s.restore(saved)
        return None
    # Post-noun letter placement: a bare ``osa`` directly at the cursor, the part
    # letter (``A`` / ``B``) trailing the noun.
    if (t := s.peek()) and t.cat == "OSA":
        s.pos += 1
        letter = _letter(s) or ""
        return (letter, "")
    s.restore(saved)
    return None


def _appendix_tail_has_selector(s: Stream) -> bool:
    """True iff an appendix part-selector / coordinated sibling reachably follows.

    Scans a short window of descriptive content (``olevan``, ``maksutaulukon``,
    separators) from the cursor; the tail is entered only when a ``<num> osa`` or a
    coordinated ``<num> liitteen`` is found before any hard boundary. Pure lookahead
    (no advance).
    """
    saved = s.save()
    seen = 0
    found = False
    while seen < _APPENDIX_PART_ENTRY_WINDOW:
        t = s.peek()
        if t is None or t.cat in _APPENDIX_TAIL_BOUNDARY_CATS:
            break
        if t.cat in ("OSA", "LIITE"):
            found = True
            break
        s.pos += 1
        seen += 1
    s.restore(saved)
    return found


def _appendix_subpart_tail(s: Stream) -> list[tuple[str, str]]:
    """Consume the appendix sub-edit tail, returning one (number, suffix) per selector.

    Called right after the base ``LIITE`` (and any attached number list) was
    matched. Walks the descriptive tail, collecting a label for each ``<num> osa``
    part-selector and each coordinated ``<num> liitteen`` sibling, until a hard
    boundary. Only fires when a selector actually follows (the caller gates with
    :func:`_appendix_tail_has_selector`).
    """
    parts: list[tuple[str, str]] = []
    while True:
        t = s.peek()
        if t is None or t.cat in _APPENDIX_TAIL_BOUNDARY_CATS:
            break
        # A ``<num> osa[n]`` part-selector.
        sel = _appendix_part_selector(s)
        if sel is not None:
            parts.append(sel)
            continue
        # A coordinated ``<num> liitteen`` sibling appendix.
        if t.cat == "LIITE":
            s.pos += 1
            post = _number_list(s)
            if post:
                parts.extend(post)
            else:
                parts.append(("", ""))
            continue
        if t.cat in _APPENDIX_TAIL_SKIP_CATS:
            s.pos += 1
            continue
        break
    return parts


def _appendix_ref(s: Stream, verb: SourceVerb) -> Optional[list[SurfaceNode]]:
    """Parse appendix reference: [number_list] LIITE [number_list] [part-selectors]."""
    saved = s.save()
    pre_nums = _number_list(s)
    if not ((t := s.peek()) and t.cat == "LIITE"):
        s.restore(saved)
        return None
    s.pos += 1
    post_nums = _number_list(s) if not pre_nums else None
    nums = pre_nums or post_nums
    nodes: list[SurfaceNode]
    if nums:
        nodes = [
            SurfaceTargetRef(
                kind=TargetKind.APPENDIX,
                label=n + sf,
                witness=_make_witness("fi.appendix_ref", saved, s.pos),
            )
            for n, sf in nums
        ]
    else:
        nodes = [
            SurfaceTargetRef(
                kind=TargetKind.APPENDIX,
                label="",
                witness=_make_witness("fi.appendix_ref", saved, s.pos),
            )
        ]
    # Appendix sub-edit tail: ``<num> osa`` part-selectors and/or coordinated
    # ``<num> liitteen`` siblings the base ref would otherwise drop. Gated so a
    # plain whole-appendix ref is untouched. The whole ref (base + tail) shares one
    # witness span so the tail tokens are covered.
    if _appendix_tail_has_selector(s):
        tail_start = s.save()
        tail_parts = _appendix_subpart_tail(s)
        if tail_parts:
            tail_w = _make_witness("fi.appendix_ref", saved, s.pos)
            base_labels = [
                n.label if isinstance(n, SurfaceTargetRef) else "" for n in nodes
            ]
            nodes = [
                SurfaceTargetRef(kind=TargetKind.APPENDIX, label=lbl, witness=tail_w)
                for lbl in base_labels
            ]
            nodes.extend(
                SurfaceTargetRef(
                    kind=TargetKind.APPENDIX, label=n + sf, witness=tail_w
                )
                for n, sf in tail_parts
            )
        else:
            s.restore(tail_start)
    return nodes


# ---- Insertion patterns (table-driven) ----


def _insertion_sub_target(
    s: Stream, verb: SourceVerb, sec: str, chapter: str, part: str, mom_ctx: int
) -> Optional[list[SurfaceNode]]:
    """After 'uusi', parse what is being inserted. Returns SurfaceNode list.

    Emits SurfaceInsertion nodes for insertion targets (momentti, kohta,
    section, chapter insertions).  Heading insertions ("uusi otsikko")
    still emit SurfaceTargetRef because they represent heading amendments,
    not structural insertions.
    """
    saved = s.save()

    # Skip archaic "näin kuuluva" / "näin kuluva" between uusi and target.
    _skip_archaic_nain_kuuluva(s)

    # "uusi otsikko" / "uusi väliotsikko" — heading insertion without number
    # May be followed by "ja N momentti" for a combined heading+subsection insert.
    if (t := s.peek()) and t.cat == "OTSIKKO":
        s.pos += 1
        nodes: list[SurfaceNode] = [
            SurfaceInsertion(
                kind=TargetKind.SECTION,
                label=sec,
                chapter=chapter,
                part=part,
                sub_target=SurfaceSubRef(facet=FacetKind.HEADING),
            )
        ]
        # Check for "ja N momentti" / "sekä N momentti" continuation
        saved_cont = s.save()
        if _sep(s) is not None:
            cont_nums = _number_list(s)
            if cont_nums and (t2 := s.peek()) and t2.cat == "MOMENTTI":
                s.pos += 1
                for n, sf in cont_nums:
                    for rn in _expand_range_single(n):
                        nodes.append(
                            SurfaceInsertion(
                                kind=TargetKind.SECTION,
                                label=sec,
                                chapter=chapter,
                                part=part,
                                sub_target=SurfaceSubRef(momentti=int(rn) if rn.isdigit() else 0),
                            )
                        )
            else:
                s.restore(saved_cont)
        else:
            s.restore(saved_cont)
        return nodes

    nums = _number_list(s)
    if not nums:
        # Try letter-only items: "uusi b kohta"
        let = _letter(s)
        if let and (t := s.peek()) and t.cat == "KOHTA":
            s.pos += 1
            eff_mom = mom_ctx or 1
            return [
                SurfaceInsertion(
                    kind=TargetKind.SECTION,
                    label=sec,
                    chapter=chapter,
                    part=part,
                    sub_target=SurfaceSubRef(momentti=eff_mom, item=let),
                )
            ]
        s.restore(saved)
        return None

    t = s.peek()
    # "uusi N momentin M kohta" — genitive momentti is a container qualifier for
    # a kohta insertion into the existing momentti N, not an insertion of momentti
    # N itself.  Mirrors the REPLACE shape "N §:n M momentin K kohta".  Only the
    # genitive ("momentin") carries this reading; illative ("momenttiin") and
    # nominative ("momentti") still insert the momentti itself.
    if t and t.cat == "MOMENTTI" and t.case == "GEN" and nums[0][0].isdigit():
        saved_mom_kohta = s.save()
        s.pos += 1
        kohta_nums = _number_list(s)
        if kohta_nums and (t2 := s.peek()) and t2.cat == "KOHTA":
            s.pos += 1
            mom = int(nums[0][0])
            nodes: list[SurfaceNode] = []
            for kn, ksf in kohta_nums:
                for rk in _expand_range_single(kn):
                    nodes.append(
                        SurfaceInsertion(
                            kind=TargetKind.SECTION,
                            label=sec,
                            chapter=chapter,
                            part=part,
                            sub_target=SurfaceSubRef(momentti=mom, item=rk + ksf),
                        )
                    )
            return nodes
        s.restore(saved_mom_kohta)
    if t and t.cat == "MOMENTTI":
        s.pos += 1
        nodes: list[SurfaceNode] = []
        for n, sf in nums:
            for rn in _expand_range_single(n):
                nodes.append(
                    SurfaceInsertion(
                        kind=TargetKind.SECTION,
                        label=sec,
                        chapter=chapter,
                        part=part,
                        sub_target=SurfaceSubRef(momentti=int(rn) if rn.isdigit() else 0),
                    )
                )
        return nodes

    if t and t.cat == "KOHTA":
        s.pos += 1
        eff_mom = mom_ctx or 1
        nodes = []
        for n, sf in nums:
            for rn in _expand_range_single(n):
                nodes.append(
                    SurfaceInsertion(
                        kind=TargetKind.SECTION,
                        label=sec,
                        chapter=chapter,
                        part=part,
                        sub_target=SurfaceSubRef(momentti=eff_mom, item=rn + sf),
                    )
                )
        return nodes

    if t and t.cat == "PYKALA":
        s.pos += 1
        return [
            SurfaceInsertion(
                kind=TargetKind.SECTION,
                label=n + sf,
                chapter=chapter,
                part=part,
            )
            for n, sf in nums
        ]

    if t and t.cat == "LUKU":
        s.pos += 1
        return [
            SurfaceInsertion(
                kind=TargetKind.CHAPTER,
                label=n + sf,
                part=part,
            )
            for n, sf in nums
        ]

    s.restore(saved)
    return None


def _chapter_heading_insert_with_tail(
    s: Stream,
    *,
    part: str = "",
) -> Optional[list[SurfaceNode]]:
    """Parse ``uusi N luvun otsikko [ja M §]`` insertion batches.

    This covers law-level insertions that introduce a new chapter heading and a
    sibling section range in the same clause, for example:

      ``uusi 3 luvun otsikko ja 15—18 §``
    """
    saved = s.save()

    chapter_nums = _number_list(s)
    if not chapter_nums or len(chapter_nums) != 1:
        s.restore(saved)
        return None

    t = s.peek()
    if not (t and t.cat == "LUKU" and t.case == "GEN"):
        s.restore(saved)
        return None
    s.pos += 1

    t = s.peek()
    if not (t and t.cat == "OTSIKKO"):
        s.restore(saved)
        return None
    s.pos += 1

    chapter_label = chapter_nums[0][0] + chapter_nums[0][1]
    nodes: list[SurfaceNode] = [
        SurfaceTargetRef(
            kind=TargetKind.CHAPTER,
            label=chapter_label,
            part=part,
            sub_refs=(SurfaceSubRef(facet=FacetKind.HEADING),),
        )
    ]

    while True:
        saved_tail = s.save()
        if _sep(s) is None:
            s.restore(saved_tail)
            break

        tail_nums = _number_list(s)
        tail_kind = s.peek()
        if not tail_nums or tail_kind is None or tail_kind.cat not in ("PYKALA", "LUKU") or tail_kind.case == "GEN":
            s.restore(saved_tail)
            break

        s.pos += 1
        if tail_kind.cat == "PYKALA":
            nodes.extend(
                SurfaceInsertion(
                    kind=TargetKind.SECTION,
                    label=n + sf,
                    chapter=chapter_label,
                    part=part,
                )
                for n, sf in tail_nums
            )
            continue

        nodes.extend(
            SurfaceInsertion(
                kind=TargetKind.CHAPTER,
                label=n + sf,
                part=part,
            )
            for n, sf in tail_nums
        )

    return nodes


def _heading_placement_after_uusi(
    s: Stream,
    *,
    chapter: str = "",
    part: str = "",
) -> Optional[list[SurfaceNode]]:
    """Parse ``uusi väliotsikko N §:n edelle`` after ``uusi`` has been consumed."""
    saved = s.save()

    t = s.peek()
    if not (t and t.cat == "OTSIKKO"):
        s.restore(saved)
        return None
    s.pos += 1

    nums = _number_list(s)
    if not nums or len(nums) != 1:
        s.restore(saved)
        return None

    t = s.peek()
    if not (t and t.cat == "PYKALA" and t.case == "GEN"):
        s.restore(saved)
        return None
    s.pos += 1

    t = s.peek()
    if not (t and t.cat == "EDELLA"):
        s.restore(saved)
        return None
    s.pos += 1

    label = nums[0][0] + nums[0][1]
    return [
        SurfaceHeadingPlacement(
            target_section=label,
            chapter=chapter,
            part=part,
            witness=_make_witness("fi.heading_edelle_otsikko_after_uusi", saved, s.pos),
        )
    ]


def _trailing_heading_placement_arm(
    s: Stream,
    *,
    chapter: str = "",
    part: str = "",
) -> Optional[list[SurfaceNode]]:
    """Parse a target-first ``<num_list> §:n edelle uusi väliotsikko`` arm.

    Unlike ``_heading_placement_after_uusi`` (which parses the ``uusi``-first
    order and only a single target), this consumes the order seen inside dense
    coordinated insert enumerations, where the section target precedes
    ``edelle uusi (väli)otsikko`` and may be a list or em-dash range:

      ``69 b–69 e ja 69 g–69 i §:n edelle uusi väliotsikko``

    One subheading is emitted per coordinated section target.  The caller is
    responsible for having already consumed the separator (and any optional
    ``uusi`` token) preceding this arm.  Returns ``None`` (restoring the
    stream) when the arm does not match, so the caller can try other forms.
    """
    saved = s.save()

    nums = _number_list(s)
    if not nums:
        s.restore(saved)
        return None

    t = s.peek()
    if not (t and t.cat == "PYKALA" and t.case == "GEN"):
        s.restore(saved)
        return None
    s.pos += 1

    t = s.peek()
    if not (t and t.cat == "EDELLA"):
        s.restore(saved)
        return None
    s.pos += 1

    # ``uusi`` is optional here — the caller may already have consumed it.
    _uusi(s)

    t = s.peek()
    if not (t and t.cat == "OTSIKKO"):
        s.restore(saved)
        return None
    s.pos += 1

    return [
        SurfaceHeadingPlacement(
            target_section=n + sf,
            chapter=chapter,
            part=part,
            witness=_make_witness("fi.heading_edelle_otsikko_target_list", saved, s.pos),
        )
        for n, sf in nums
    ]


def _postfix_chapter_section_inserts(
    s: Stream, part: str = ""
) -> Optional[list[SurfaceNode]]:
    """Parse a list of ``<num> § lukuun <chap>`` postfix-chapter insert arms.

    After ``lisätään ... uusi`` the new section can carry its destination
    chapter AFTER the section, named for the section it joins:

      ``uusi 35 a § lukuun 5, 104 a § lukuun 6 ja 133 b § lukuun 7``   (§ lukuun N)
      ``uusi 35 c § 5 lukuun``                                         (§ N lukuun)

    Each arm yields a ``SurfaceInsertion`` of kind SECTION whose ``chapter`` is
    the named chapter.  Without this the generic insert path consumed only the
    bare ``§`` of the first arm, dropping the chapter destination and truncating
    the remaining arms.

    Returns None (restoring the stream) unless at least one full
    ``<num> § lukuun <chap>`` arm is present, so the generic insert path keeps
    handling section inserts that have no postfix chapter.
    """
    saved = s.save()

    def _arm() -> Optional[list[tuple[str, str]]]:
        """Parse one arm; return [(section_label, chapter_label), ...] or None.

        An arm is a (possibly coordinated) section list sharing one postfix
        chapter, e.g. ``103 a, 103 b ja 104 b § 6 lukuun`` -> three sections
        all in chapter 6.
        """
        arm_saved = s.save()
        nums = _number_list(s)
        if not nums:
            return None
        t = s.peek()
        if not (t and t.cat == "PYKALA" and t.case != "GEN"):
            s.restore(arm_saved)
            return None
        s.pos += 1  # consume §
        t = s.peek()
        chap: Optional[str] = None
        # Shape A: § lukuun N  (LUKU:ILL precedes its number)
        if t and t.cat == "LUKU" and t.case == "ILL":
            s.pos += 1
            cnum = _number_with_suffix(s)
            if cnum is not None:
                chap = cnum[0] + cnum[1]
        # Shape B: § N lukuun  (number precedes LUKU:ILL)
        elif t and t.cat == "NUM":
            saved_b = s.save()
            cnum = _number_with_suffix(s)
            t2 = s.peek()
            if cnum is not None and t2 and t2.cat == "LUKU" and t2.case == "ILL":
                s.pos += 1
                chap = cnum[0] + cnum[1]
            else:
                s.restore(saved_b)
        if chap is None:
            s.restore(arm_saved)
            return None
        labels: list[tuple[str, str]] = []
        for n, sf in nums:
            for rn in _expand_range_single(n):
                full = rn + (sf if len(_expand_range_single(n)) == 1 else "")
                labels.append((full, chap))
        return labels

    first = _arm()
    if first is None:
        s.restore(saved)
        return None
    arms: list[tuple[str, str]] = list(first)
    while True:
        sep_saved = s.save()
        if _sep(s) is None:
            break
        _uusi(s)  # optional repeated "uusi" before each arm
        nxt = _arm()
        if nxt is None:
            s.restore(sep_saved)
            break
        arms.extend(nxt)
    _w = _make_witness("fi.insertion_section_postfix_chapter", saved, s.pos)
    return [
        SurfaceInsertion(
            kind=TargetKind.SECTION,
            label=sec,
            chapter=chap,
            part=part,
            witness=_w,
        )
        for sec, chap in arms
    ]


def _insertion(s: Stream, verb: SourceVerb, chapter: str, part: str = "") -> Optional[list[SurfaceNode]]:
    """Parse all insertion patterns. Returns SurfaceNode list."""
    saved = s.save()

    # Pre-parse optional container context for insertions.
    part_pre = _part_ctx(s) or ""
    ch_pre = _chapter_ctx(s) or ""
    effective_part = part_pre or part
    effective_chapter = ch_pre or chapter

    # Skip REINST_SPAN / CITATION_SPAN from tag-not-delete filters.
    _had_reinst = (t := s.peek()) and t.cat in _REINST_OR_CITE
    if _had_reinst:
        s.skip_cats(_REINST_OR_CITE)
        saved = s.save()

    # Pattern A-1: number OSA:ILL uusi number_list (PYKALA | LUKU)
    # Handles explicit part-scoped insertion groups like:
    #   "V osaan uusi 2 ja 3 luku"
    saved_part_ill = s.save()
    part_nums = _number_list(s)
    if part_nums and len(part_nums) == 1:
        t = s.peek()
        if t and t.cat == "OSA" and t.case == "ILL":
            s.pos += 1
            explicit_part = part_nums[0][0] + part_nums[0][1]
            if (t := s.peek()) and t.cat == "COMMA":
                s.pos += 1
            if (t := s.peek()) and t.cat in _TILALLE_OR_REINST:
                s.pos += 1
            if _uusi(s):
                _skip_archaic_nain_kuuluva(s)
                ins_nums = _number_list(s)
                pt = s.peek()
                if ins_nums and pt and pt.cat in ("PYKALA", "LUKU") and pt.case != "GEN":
                    kind = TargetKind.SECTION if pt.cat == "PYKALA" else TargetKind.CHAPTER
                    s.pos += 1
                    return [SurfaceInsertion(kind=kind, label=n + sf, chapter="", part=explicit_part) for n, sf in ins_nums]
            elif verb == SourceVerb.LISATA:
                ins_nums = _number_list(s)
                pt = s.peek()
                if ins_nums and pt and pt.cat in ("PYKALA", "LUKU") and pt.case != "GEN":
                    kind = TargetKind.SECTION if pt.cat == "PYKALA" else TargetKind.CHAPTER
                    s.pos += 1
                    return [SurfaceInsertion(kind=kind, label=n + sf, chapter="", part=explicit_part) for n, sf in ins_nums]
    s.restore(saved_part_ill)

    # Pattern A0: OSA:ILL uusi number_list (PYKALA | LUKU)
    # Handles inherited-part continuation groups like:
    #   "II osan ... 3 lukuun uusi 3-15 § ja osaan uusi 4-13 luku"
    # where the continuation omits the explicit part numeral.
    t = s.peek()
    if t and t.cat == "OSA" and t.case == "ILL" and effective_part:
        s.pos += 1
        if (t := s.peek()) and t.cat == "COMMA":
            s.pos += 1
        if (t := s.peek()) and t.cat in _TILALLE_OR_REINST:
            s.pos += 1
        if _uusi(s):
            _skip_archaic_nain_kuuluva(s)
            ins_nums = _number_list(s)
            pt = s.peek()
            if ins_nums and pt and pt.cat in ("PYKALA", "LUKU") and pt.case != "GEN":
                kind = TargetKind.SECTION if pt.cat == "PYKALA" else TargetKind.CHAPTER
                s.pos += 1
                return [SurfaceInsertion(kind=kind, label=n + sf, chapter="", part=effective_part) for n, sf in ins_nums]
        elif verb == SourceVerb.LISATA:
            ins_nums = _number_list(s)
            pt = s.peek()
            if ins_nums and pt and pt.cat in ("PYKALA", "LUKU") and pt.case != "GEN":
                kind = TargetKind.SECTION if pt.cat == "PYKALA" else TargetKind.CHAPTER
                s.pos += 1
                return [SurfaceInsertion(kind=kind, label=n + sf, chapter="", part=effective_part) for n, sf in ins_nums]
        s.restore(saved)

    # Pattern A: number_list §:ILL [reinst] uusi sub_target
    nums = _number_list(s)
    if nums:
        t = s.peek()
        if t and t.cat == "PYKALA" and t.case == "ILL":
            s.pos += 1
            # Skip comma + reinstatement/tilalle/citation/provenance before uusi
            if (t := s.peek()) and t.cat == "COMMA":
                s.pos += 1
            s.skip_cats(_TILALLE_OR_REINST | frozenset({"PROVENANCE_SPAN"}))
            if (t := s.peek()) and t.cat == "COMMA":
                s.pos += 1
            # Skip "N momentin/kohdan tilalle" reinstatement preamble
            saved_rt = s.save()
            _rn = _number_list(s)
            if _rn and (t := s.peek()) and t.cat in ("MOMENTTI", "KOHTA"):
                s.pos += 1
                if (t := s.peek()) and t.cat in _TILALLE_OR_REINST:
                    s.pos += 1
                else:
                    s.restore(saved_rt)
            elif _rn:
                s.restore(saved_rt)
            # Skip "b kohdan tilalle" or similar (letter-keyed items)
            saved_kt = s.save()
            _let = _letter(s)
            if _let and (t := s.peek()) and t.cat == "KOHTA":
                s.pos += 1
                if (t := s.peek()) and t.cat in _TILALLE_OR_REINST:
                    s.pos += 1
                else:
                    s.restore(saved_kt)
            elif _let:
                s.restore(saved_kt)
            # Skip an archaic "näin kuuluva" lead-in that can sit between the
            # §:ään target (and its skipped provenance/citation span) and "uusi",
            # e.g. "7 §:ään, sellaisena kuin se on ..., näin kuuluva uusi 3 momentti".
            _skip_archaic_nain_kuuluva(s)
            if _uusi(s):
                sec_nums = [n + sf for n, sf in nums]
                all_nodes: list[SurfaceNode] = []
                for sec in sec_nums:
                    saved_sub = s.save()
                    sub_nodes = _insertion_sub_target(s, verb, sec, effective_chapter, effective_part, 0)
                    if sub_nodes:
                        all_nodes.extend(sub_nodes)
                    if sec != sec_nums[-1]:
                        s.restore(saved_sub)
                if all_nodes:
                    # Check for chained "sekä/ja [uusi] <sub_target>".  The repeated
                    # "uusi" is optional for anaphoric continuations that share the
                    # same section and the same "uusi": e.g. "N §:ään uusi 1 momentin
                    # 4 kohta ja 4 momentti" — the second "4 momentti" inserts into
                    # the same section.  We require the continuation to be a bare
                    # sub-target (N momentti / N kohta / b kohta) when "uusi" is
                    # absent, so a following fresh target such as "ja lakiin uusi
                    # 102 b §" (DOC:ILL) or "ja N §:ään uusi ..." is left for the
                    # outer target-list loop.
                    while True:
                        saved_c = s.save()
                        if _sep(s) is None:
                            s.restore(saved_c)
                            break
                        had_uusi = _uusi(s) is not None
                        if not had_uusi:
                            # Only allow anaphoric continuation when the next token
                            # directly begins a bare sub-target, not a new section
                            # or law-level target (DOC/PYKALA/LUKU/OSA/section ref).
                            nxt = s.peek()
                            if nxt is None or nxt.cat not in ("NUM", "LETTER"):
                                s.restore(saved_c)
                                break
                        batch: list[SurfaceNode] = []
                        for sec in sec_nums:
                            saved_sub = s.save()
                            more = _insertion_sub_target(s, verb, sec, effective_chapter, effective_part, 0)
                            if more:
                                batch.extend(more)
                            if sec != sec_nums[-1]:
                                s.restore(saved_sub)
                        # For the bare (no-"uusi") anaphoric form, the continuation
                        # must be a genuine sub-target (momentti/kohta) of the same
                        # section — not a fresh "N §" / "N luku" target that happens
                        # to parse via the section/chapter arms of the sub-target
                        # rule.  Otherwise "ja 102 §:ään ..." would be absorbed.
                        if not had_uusi and not all(
                            isinstance(n, SurfaceInsertion)
                            and n.sub_target is not None
                            and (n.sub_target.momentti or n.sub_target.item or n.sub_target.facet)
                            for n in batch
                        ):
                            s.restore(saved_c)
                            break
                        if not batch:
                            # Continuation did not parse as a sub-target; rewind so
                            # the outer loop can treat it as a fresh target.
                            s.restore(saved_c)
                            break
                        all_nodes.extend(batch)
                    return all_nodes

        # Pattern B2: number §:GEN number MOMENTTI:ILL [reinst] uusi sub_target
        # and the historical genitive variant
        # number §:GEN number MOMENTTI:GEN uusi sub_target.
        #
        # Finland sources use both:
        #   ``27 §:n 2 momenttiin uusi 5 kohta``
        #   ``27 §:n 2 momentin uusi 5 kohta``
        #
        # The latter is one insertion arm, not a plain target reference followed
        # by an orphaned bare ``uusi`` tail. Parse it here so the target list
        # does not truncate at the target-ref boundary.
        if t and t.cat == "PYKALA" and t.case == "GEN":
            s.pos += 1
            m_nums = _number_list(s)
            if m_nums and (t := s.peek()) and t.cat == "MOMENTTI" and t.case in ("ILL", "GEN"):
                s.pos += 1
                m_num = int(m_nums[0][0]) if m_nums[0][0].isdigit() else 0
                if (t := s.peek()) and t.cat == "COMMA":
                    s.pos += 1
                s.skip_cats(_REINST_OR_CITE | frozenset({"PROVENANCE_SPAN"}))
                # Skip reinstatement preamble
                saved_b2 = s.save()
                _skip_to_uusi = False
                if (t := s.peek()) and t.cat in ("LETTER", "NUM"):
                    _ri = s.pos
                    while _ri < len(s.tokens) and s.tokens[_ri].cat != "UUSI":
                        if s.tokens[_ri].cat in _TILALLE_OR_REINST:
                            s.pos = _ri + 1
                            _skip_to_uusi = True
                            break
                        if s.tokens[_ri].cat == "VERB":
                            break
                        _ri += 1
                    if not _skip_to_uusi:
                        s.restore(saved_b2)
                if _uusi(s):
                    sec_nums = [n + sf for n, sf in nums]
                    all_nodes = []
                    for sec in sec_nums:
                        saved_sub = s.save()
                        sub_nodes = _insertion_sub_target(s, verb, sec, effective_chapter, effective_part, m_num)
                        if sub_nodes:
                            all_nodes.extend(sub_nodes)
                        if sec != sec_nums[-1]:
                            s.restore(saved_sub)
                    if all_nodes:
                        return all_nodes

            # Pattern B3: number §:GEN uusi sub_target
            # Covers "4 §:n uusi 2 momentti seuraavasti" — nominative "uusi N momentti"
            # names the new sub-element being inserted.  Unlike B2, there is no
            # intervening MOMENTTI:ILL; the genitive §:n directly precedes "uusi".
            if not m_nums and _uusi(s):
                sec_nums = [n + sf for n, sf in nums]
                all_nodes = []
                for sec in sec_nums:
                    saved_sub = s.save()
                    sub_nodes = _insertion_sub_target(s, verb, sec, effective_chapter, effective_part, 0)
                    if sub_nodes:
                        all_nodes.extend(sub_nodes)
                    if sec != sec_nums[-1]:
                        s.restore(saved_sub)
                if all_nodes:
                    return all_nodes

        # Pattern F: number LUKU:ILL [,] [tilalle] uusi number_list PYKALA
        if t and t.cat == "LUKU" and t.case == "ILL":
            s.pos += 1
            chap_num = nums[0][0] + nums[0][1]
            if (t := s.peek()) and t.cat == "COMMA":
                s.pos += 1
            if (t := s.peek()) and t.cat in _TILALLE_OR_REINST:
                s.pos += 1
            # Skip residual number + §:GEN + tilalle from partial reinstatement
            saved_rf = s.save()
            _rn = _number_list(s)
            if _rn and (t := s.peek()) and t.cat == "PYKALA" and t.case == "GEN":
                s.pos += 1
                if (t := s.peek()) and t.cat in _TILALLE_OR_REINST:
                    s.pos += 1
                else:
                    s.restore(saved_rf)
            elif _rn:
                s.restore(saved_rf)
            # Skip heading placement
            _heading_placement = seq(
                lambda s: _number_list(s),
                cat_case("PYKALA", "GEN"),
                cat("EDELLA"),
                _uusi,
                cat("OTSIKKO"),
                opt(_sep),
            )
            _heading_placement_luku = seq(
                lambda s: _number_list(s),
                cat_case("PYKALA", "GEN"),
                cat("EDELLA"),
                _uusi,
                cat_case("LUKU", "GEN"),
                cat("OTSIKKO"),
                opt(_sep),
            )
            if not _heading_placement(s):
                _heading_placement_luku(s)
            _skip_reinstatement_preamble_to_uusi(s)
            if _uusi(s):
                _skip_archaic_nain_kuuluva(s)
                ins_nums = _number_list(s)
                pt = s.peek()
                if ins_nums and pt and pt.cat in ("PYKALA", "LUKU"):
                    kind = TargetKind.SECTION if pt.cat == "PYKALA" else TargetKind.CHAPTER
                    s.pos += 1
                    return [
                        SurfaceInsertion(kind=kind, label=n + sf, chapter=chap_num, part=effective_part)
                        for n, sf in ins_nums
                    ]
            # Fallback: N LUKU:ILL NUM § without uusi (implied by lisätään)
            elif verb == SourceVerb.LISATA:
                ins_nums = _number_list(s)
                pt = s.peek()
                if ins_nums and pt and pt.cat in ("PYKALA", "LUKU") and pt.case != "GEN":
                    kind = TargetKind.SECTION if pt.cat == "PYKALA" else TargetKind.CHAPTER
                    s.pos += 1
                    return [
                        SurfaceInsertion(kind=kind, label=n + sf, chapter=chap_num, part=effective_part)
                        for n, sf in ins_nums
                    ]

        # Pattern G: OSA:ILL uusi number_list (PYKALA | LUKU)
        # Handles inherited-part continuation groups like:
        #   "II osan ... 3 lukuun uusi 3-15 § ja osaan uusi 4-13 luku"
        # where the second arm omits the explicit part numeral but still
        # targets the current part context.
        if t and t.cat == "OSA" and t.case == "ILL" and part:
            s.pos += 1
            if (t := s.peek()) and t.cat == "COMMA":
                s.pos += 1
            if (t := s.peek()) and t.cat in _TILALLE_OR_REINST:
                s.pos += 1
            if _uusi(s):
                _skip_archaic_nain_kuuluva(s)
                ins_nums = _number_list(s)
                pt = s.peek()
                if ins_nums and pt and pt.cat in ("PYKALA", "LUKU") and pt.case != "GEN":
                    kind = TargetKind.SECTION if pt.cat == "PYKALA" else TargetKind.CHAPTER
                    s.pos += 1
                    return [SurfaceInsertion(kind=kind, label=n + sf, chapter="", part=part) for n, sf in ins_nums]
            elif verb == SourceVerb.LISATA:
                ins_nums = _number_list(s)
                pt = s.peek()
                if ins_nums and pt and pt.cat in ("PYKALA", "LUKU") and pt.case != "GEN":
                    kind = TargetKind.SECTION if pt.cat == "PYKALA" else TargetKind.CHAPTER
                    s.pos += 1
                    return [SurfaceInsertion(kind=kind, label=n + sf, chapter="", part=part) for n, sf in ins_nums]

    s.restore(saved)

    # Pattern C: DOC:ILL uusi number_list (PYKALA | LUKU)
    # Also handles: DOC:ILL uusi N §:n M momentti (subsection insertion via doc-type)
    #               DOC:ILL uusi liite [N] (appendix insertion)
    # NB: All ops produced by Pattern C have chapter="" — DOC:ILL (asetukseen/
    # lakiin) returns to statute level.
    t = s.peek()
    if t and t.cat == "DOC" and t.case == "ILL":
        s.pos += 1
        if (t := s.peek()) and t.cat == "COMMA":
            s.pos += 1
        s.skip_cats(_REINST_OR_CITE | frozenset({"PROVENANCE_SPAN"}))
        if (t := s.peek()) and t.cat == "PROV":
            s.pos = _skip_prov_span(s.tokens, s.pos, len(s.tokens))
            s.skip_cats(frozenset({"PROVENANCE_SPAN"}))
        _skip_named_heading_anchor_before_insert(s)
        if _uusi(s):
            _skip_archaic_nain_kuuluva(s)
            heading_nodes = _chapter_heading_insert_with_tail(s)
            if heading_nodes:
                return heading_nodes
            heading_placement_nodes = _heading_placement_after_uusi(s, chapter="", part=part)
            if heading_placement_nodes:
                return heading_placement_nodes
            # DOC:ILL uusi liite [N] — appendix insertion
            if (t := s.peek()) and t.cat == "LIITE":
                s.pos += 1
                post_nums = _number_list(s)
                if post_nums:
                    return [SurfaceInsertion(kind=TargetKind.APPENDIX, label=n + sf) for n, sf in post_nums]
                return [SurfaceInsertion(kind=TargetKind.APPENDIX, label="")]
            # DOC:ILL uusi <num> § lukuun <chap> [, <num> § lukuun <chap>]...
            # The new section's destination chapter follows the section
            # (postfix), named for the section it joins.
            postfix_chapter_nodes = _postfix_chapter_section_inserts(s, part=part)
            if postfix_chapter_nodes:
                return postfix_chapter_nodes
            nums2 = _number_list(s)
            if nums2:
                # Collect chained "sekä/ja uusi range" groups before final §
                all_nums = list(nums2)
                while True:
                    saved_chain = s.save()
                    if _sep(s) is not None and _uusi(s) is not None:
                        more = _number_list(s)
                        if more:
                            all_nums.extend(more)
                            continue
                    s.restore(saved_chain)
                    break
                pt = s.peek()
                # DOC:ILL uusi number_list OSA — whole-part insertion
                if pt and pt.cat == "OSA":
                    s.pos += 1
                    return [SurfaceInsertion(kind=TargetKind.PART, label=n + sf, chapter="") for n, sf in all_nums]
                if pt and pt.cat in ("PYKALA", "LUKU"):
                    if pt.case != "GEN":
                        malformed_chapter_insert = (
                            pt.cat == "PYKALA"
                            and (t1 := s.peek(1)) is not None
                            and t1.cat == "LUKU"
                            and t1.case == "NOM"
                        )
                        kind = (
                            TargetKind.CHAPTER
                            if malformed_chapter_insert
                            else (TargetKind.SECTION if pt.cat == "PYKALA" else TargetKind.CHAPTER)
                        )
                        s.pos += 1
                        if malformed_chapter_insert:
                            s.pos += 1
                        insert_nodes: list[SurfaceNode] = [
                            SurfaceInsertion(kind=kind, label=n + sf, chapter="") for n, sf in all_nums
                        ]
                        # Continuation: a plain-§ insert group may be followed by
                        # one or more trailing arms joined by sekä/ja:
                        #   - "<num> § lukuun <chap>" postfix-chapter group
                        #     ("uusi 21 a ja 21 b § sekä 35 c § 5 lukuun ...")
                        #   - "<num_list> §:n edelle uusi väliotsikko" heading
                        #     placement, where the target may be a list/range
                        #     ("sekä 69 b–69 e ja 69 g–69 i §:n edelle uusi
                        #     väliotsikko"); the multi-target form used to abort
                        #     the whole enumeration silently (2009/886 over
                        #     1501/1993 lost 69j/69k/69l/71 §/138 §).
                        #   - a further "[uusi] <num_list> §" insert group after
                        #     a heading arm ("..., lakiin uusi 69 j ja 69 k §").
                        if kind == TargetKind.SECTION and not malformed_chapter_insert:
                            while True:
                                cont_saved = s.save()
                                if _sep(s) is None:
                                    s.restore(cont_saved)
                                    break
                                # Optional DOC:ILL re-anchor ("..., lakiin uusi ...").
                                if (dt := s.peek()) and dt.cat == "DOC" and dt.case == "ILL":
                                    s.pos += 1
                                _uusi(s)
                                heading_arm = _trailing_heading_placement_arm(s, part=part)
                                if heading_arm:
                                    insert_nodes.extend(heading_arm)
                                    continue
                                tail = _postfix_chapter_section_inserts(s, part=part)
                                if tail:
                                    insert_nodes.extend(tail)
                                    continue
                                # A further bare "<num_list> §" insert group, but
                                # ONLY a nominative whole-section target.  An
                                # illative "§:ään" ("into section N, uusi M
                                # momentti/kohta") or genitive "§:n" is a
                                # sub-target insert and must be left for the
                                # sub-target path, not grabbed as a section.
                                more_nums = _number_list(s)
                                if (
                                    more_nums
                                    and (pt2 := s.peek())
                                    and pt2.cat == "PYKALA"
                                    and pt2.case == "NOM"
                                ):
                                    s.pos += 1
                                    insert_nodes.extend(
                                        SurfaceInsertion(kind=TargetKind.SECTION, label=n + sf, chapter="")
                                        for n, sf in more_nums
                                    )
                                    continue
                                s.restore(cont_saved)
                                break
                        return insert_nodes
                    # Extended: uusi N §:n M momentti/kohta (subsection insertion)
                    if pt is not None and pt.cat == "PYKALA" and pt.case == "GEN":
                        s.pos += 1  # consume §:n
                        sec_num = nums2[0][0] + nums2[0][1]
                        sub_nums = _number_list(s)
                        if sub_nums and (t := s.peek()) and t.cat in ("MOMENTTI", "KOHTA"):
                            is_kohta = t.cat == "KOHTA"
                            s.pos += 1
                            nodes = []
                            for n, sf in sub_nums:
                                for rn in _expand_range_single(n):
                                    if is_kohta:
                                        nodes.append(
                                            SurfaceInsertion(
                                                kind=TargetKind.SECTION,
                                                label=sec_num,
                                                chapter="",
                                                sub_target=SurfaceSubRef(momentti=1, item=rn + sf),
                                            )
                                        )
                                    else:
                                        nodes.append(
                                            SurfaceInsertion(
                                                kind=TargetKind.SECTION,
                                                label=sec_num,
                                                chapter="",
                                                sub_target=SurfaceSubRef(momentti=int(rn) if rn.isdigit() else 0),
                                            )
                                        )
                            return nodes
                        # No momentti/kohta follows — GEN §:n is stylistic variant
                        return [
                            SurfaceInsertion(kind=TargetKind.SECTION, label=n + sf, chapter="") for n, sf in all_nums
                        ]
        # Prefix-chapter section insert: "DOC:ILL N lukuun uusi M §".  The chapter
        # named between the document and "uusi" is the destination scope for the
        # new section, not a chapter insertion.  Without this branch the generic
        # "DOC:ILL number_list LUKU" fallback below mis-reads "N lukuun" as a
        # whole-chapter insert and drops the trailing "uusi M §".
        saved_prefix_chapter = s.save()
        prefix_chapter_nums = _number_list(s)
        if prefix_chapter_nums and len(prefix_chapter_nums) == 1:
            pc_tok = s.peek()
            if pc_tok and pc_tok.cat == "LUKU" and pc_tok.case == "ILL":
                pc_chapter = prefix_chapter_nums[0][0] + prefix_chapter_nums[0][1]
                s.pos += 1  # consume LUKU:ILL
                if (tc := s.peek()) and tc.cat == "COMMA":
                    s.pos += 1
                _skip_reinstatement_preamble_to_uusi(s)
                if _uusi(s):
                    _skip_archaic_nain_kuuluva(s)
                    pc_nums = _number_list(s)
                    pc_pt = s.peek()
                    if pc_nums and pc_pt and pc_pt.cat == "PYKALA" and pc_pt.case != "GEN":
                        s.pos += 1
                        return [
                            SurfaceInsertion(
                                kind=TargetKind.SECTION, label=n + sf, chapter=pc_chapter
                            )
                            for n, sf in pc_nums
                        ]
        s.restore(saved_prefix_chapter)

        # Fallback: DOC:ILL number_list § (no 'uusi', implied by lisätään verb)
        if verb == SourceVerb.LISATA:
            s.restore(saved)
            s.pos += 1  # re-consume DOC:ILL
            nums2 = _number_list(s)
            pt = s.peek()
            if nums2 and pt and pt.cat in ("PYKALA", "LUKU") and pt.case != "GEN":
                kind = TargetKind.SECTION if pt.cat == "PYKALA" else TargetKind.CHAPTER
                s.pos += 1
                return [SurfaceInsertion(kind=kind, label=n + sf, chapter="") for n, sf in nums2]
        s.restore(saved)
        return None

    # Pattern E: LUKU:ILL [tilalle|N §:GEN tilalle] uusi number_list PYKALA (anaphoric)
    t = s.peek()
    if t and t.cat == "LUKU" and t.case == "ILL":
        s.pos += 1
        if (t := s.peek()) and t.cat in _TILALLE_OR_REINST:
            s.pos += 1
        saved_re = s.save()
        _rn = _number_list(s)
        if _rn and (t := s.peek()) and t.cat == "PYKALA" and t.case == "GEN":
            s.pos += 1
            if (t := s.peek()) and t.cat in _TILALLE_OR_REINST:
                s.pos += 1
            else:
                s.restore(saved_re)
        elif _rn:
            s.restore(saved_re)
        if _uusi(s):
            nums2 = _number_list(s)
            pt = s.peek()
            if nums2 and pt and pt.cat == "PYKALA":
                s.pos += 1
                return [
                    SurfaceInsertion(kind=TargetKind.SECTION, label=n + sf, chapter=chapter, part=part)
                    for n, sf in nums2
                ]
        # Fallback: LUKU:ILL NUM § without uusi (implied by lisätään)
        if verb == SourceVerb.LISATA:
            s.restore(saved)
            s.pos += 1  # re-consume LUKU:ILL
            nums2 = _number_list(s)
            pt = s.peek()
            if nums2 and pt and pt.cat in ("PYKALA", "LUKU") and pt.case != "GEN":
                kind = TargetKind.SECTION if pt.cat == "PYKALA" else TargetKind.CHAPTER
                s.pos += 1
                return [SurfaceInsertion(kind=kind, label=n + sf, chapter=chapter, part=part) for n, sf in nums2]
        s.restore(saved)
        return None

    s.restore(saved)

    # Pattern D: UUSI number_list (PYKALA | LUKU) — law-level insertion where
    # citation filter already stripped "lakiin"/"asetukseen".
    # Also handles citation-stripped "uusi N §:n M momentti/kohta".
    #
    # Historical source family: authority/title lead-ins can leave a bare
    # whole-section insertion like ``päätökseen ... uusi 8 b seuraavasti:``
    # with no visible ``§`` token before the END marker.
    t = s.peek()
    if t and t.cat == "UUSI" and verb in (SourceVerb.LISATA, SourceVerb.SIIRTAA):
        s.pos += 1
        _skip_archaic_nain_kuuluva(s)
        heading_nodes = _chapter_heading_insert_with_tail(s, part=part)
        if heading_nodes:
            return heading_nodes
        heading_placement_nodes = _heading_placement_after_uusi(s, chapter=chapter, part=part)
        if heading_placement_nodes:
            return heading_placement_nodes
        nums2 = _number_list(s)
        if nums2:
            all_nums = list(nums2)
            while True:
                saved_chain = s.save()
                if _sep(s) is not None and _uusi(s) is not None:
                    more = _number_list(s)
                    if more:
                        all_nums.extend(more)
                        continue
                s.restore(saved_chain)
                break
            pt = s.peek()
            # UUSI number_list OSA — whole-part insertion (citation stripped DOC:ILL)
            if pt and pt.cat == "OSA":
                s.pos += 1
                return [SurfaceInsertion(kind=TargetKind.PART, label=n + sf, chapter=chapter) for n, sf in all_nums]
            if pt and pt.cat in ("PYKALA", "LUKU"):
                if pt.cat == "PYKALA" and pt.case == "GEN":
                    saved_gen = s.save()
                    s.pos += 1
                    sec_num = nums2[0][0] + nums2[0][1]
                    sub_nums = _number_list(s)
                    if sub_nums and (t := s.peek()) and t.cat in ("MOMENTTI", "KOHTA"):
                        is_kohta = t.cat == "KOHTA"
                        s.pos += 1
                        nodes = []
                        for n, sf in sub_nums:
                            for rn in _expand_range_single(n):
                                if is_kohta:
                                    nodes.append(
                                        SurfaceInsertion(
                                            kind=TargetKind.SECTION,
                                            label=sec_num,
                                            chapter=chapter,
                                            part=part,
                                            sub_target=SurfaceSubRef(momentti=1, item=rn + sf),
                                        )
                                    )
                                else:
                                    nodes.append(
                                        SurfaceInsertion(
                                            kind=TargetKind.SECTION,
                                            label=sec_num,
                                            chapter=chapter,
                                            part=part,
                                            sub_target=SurfaceSubRef(momentti=int(rn) if rn.isdigit() else 0),
                                        )
                                    )
                        return nodes
                    s.restore(saved_gen)
                malformed_chapter_insert = (
                    pt.cat == "PYKALA" and (t1 := s.peek(1)) is not None and t1.cat == "LUKU" and t1.case == "NOM"
                )
                kind = (
                    TargetKind.CHAPTER
                    if malformed_chapter_insert
                    else (TargetKind.SECTION if pt.cat == "PYKALA" else TargetKind.CHAPTER)
                )
                s.pos += 1
                if malformed_chapter_insert:
                    s.pos += 1
                return [SurfaceInsertion(kind=kind, label=n + sf, chapter=chapter, part=part) for n, sf in all_nums]
            if pt and pt.cat in {"END", "END_SENTINEL_SPAN"}:
                return [SurfaceInsertion(kind=TargetKind.SECTION, label=n + sf, chapter=chapter, part=part) for n, sf in all_nums]
            if pt is None and s.pos > 0 and s.tokens[s.pos - 1].cat == "END_SENTINEL_SPAN":
                return [SurfaceInsertion(kind=TargetKind.SECTION, label=n + sf, chapter=chapter, part=part) for n, sf in all_nums]
        s.restore(saved)
        return None

    s.restore(saved)
    return None


# ---- Top-level grammar ----


def _target(
    s: Stream,
    verb: SourceVerb,
    chapter: str,
    part: str = "",
    *,
    started_with_citation_span_hint: bool = False,
) -> Optional[list[SurfaceNode]]:
    """Try each target rule in priority order. Returns SurfaceNode list."""
    saved_top = s.save()

    # Skip span tokens from tag-not-delete filters that may appear before targets.
    s.skip_sentinels()
    started_with_citation_span = (
        started_with_citation_span_hint
        or (
            saved_top < len(s.tokens)
            and s.pos > saved_top
            and s.tokens[saved_top].cat == "CITATION_SPAN"
        )
    )

    def _skip_authority_nojalla_lead_in() -> bool:
        """Skip authority citation lead-ins before the real target list.

        Example:
          ``lain 6 §:n nojalla sanotun lain täytäntöönpanosta ... annetun asetuksen
          1, 3, 7, 8 ja 10 §``

        Without this skip the parser can latch onto the authority reference
        ``6 §:n`` as if it were the operative target.
        """
        # If we're already positioned at UUSI the leading CITATION_SPAN was a
        # reinstatement annotation, not an authority lead-in.  Don't skip ahead —
        # that would jump over the current insertion arm to the next one.
        # Example: "... [CITE][REINST] uusi 13 §, [CITE][REINST] uusi 14 §"
        # where skip_sentinels() has already consumed [CITE][REINST] and left us
        # at UUSI for section 13.
        if (t := s.peek()) and t.cat == "UUSI":
            return False
        saved = s.save()
        i = s.pos
        n = len(s.tokens)
        saw_nojalla = False
        saw_structural_authority = False
        anchor_words = frozenset({"annetun", "annettu", "annetuissa", "annetuilla", "annetussa"})
        while i < n:
            tok = s.tokens[i]
            if tok.cat == "VERB":
                s.restore(saved)
                return False
            if tok.cat in {"NUM", "LETTER", "DASH", "CONJ", "PYKALA", "MOMENTTI"}:
                saw_structural_authority = True
            if (saw_nojalla or started_with_citation_span) and saw_structural_authority and tok.cat == "CITATION_SPAN":
                j = i + 1
                while j < n:
                    look = s.tokens[j]
                    if look.cat in {"VERB", "END", "END_SENTINEL_SPAN"}:
                        break
                    if look.cat == "UUSI":
                        s.pos = j
                        s.skip_sentinels()
                        return True
                    j += 1
            if tok.cat == "WORD" and tok.text.lower() == "nojalla":
                saw_nojalla = True
                i += 1
                continue
            if (
                saw_nojalla
                and tok.cat == "WORD"
                and tok.text.lower() in anchor_words
                and i + 1 < n
                and s.tokens[i + 1].cat == "DOC"
                and s.tokens[i + 1].case == "GEN"
            ):
                i += 2
                s.pos = i
                s.skip_sentinels()
                return True
            if saw_nojalla and tok.cat == "COMMA":
                j = i + 1
                while j < n:
                    look = s.tokens[j]
                    if look.cat in {"VERB", "END"}:
                        break
                    if look.cat == "UUSI":
                        s.pos = j
                        s.skip_sentinels()
                        return True
                    j += 1
            i += 1
        s.restore(saved)
        return False

    if _skip_authority_nojalla_lead_in():
        result = (
            _insertion(s, verb, chapter, part=part)
            or _section_ref(s, verb, chapter, part=part)
            or _chapter_ref(s, verb, part=part)
            or _part_ref(s, verb)
            or _nimike_ref(s, verb)
            or _appendix_ref(s, verb)
        )
        if result:
            _stamp_default_witness(result, saved_top, s.pos)
            return result

    # Skip DOC:GEN before structural targets ("lain 6, 7 ja 18 §")
    if (t := s.peek()) and t.cat == "DOC" and t.case == "GEN":
        saved_doc = s.save()
        s.pos += 1
        s.skip_sentinels()
        result = (
            _insertion(s, verb, chapter, part=part)
            or _section_ref(s, verb, chapter, part=part)
            or _chapter_ref(s, verb, part=part)
            or _appendix_ref(s, verb)
            or _nimike_ref(s, verb)
        )
        if result:
            _stamp_default_witness(result, saved_top, s.pos)
            return result
        s.restore(saved_doc)

    result = (
        _insertion(s, verb, chapter, part=part)
        or _section_ref(s, verb, chapter, part=part)
        or _chapter_ref(s, verb, part=part)
        or _pykala_prefix_section_ref(s, verb, chapter, part=part)
        or _part_ref(s, verb)
        or _nimike_ref(s, verb)
        or _appendix_ref(s, verb)
    )
    if result:
        _stamp_default_witness(result, saved_top, s.pos)
        return result
    s.restore(saved_top)
    return None


def _stamp_default_witness(nodes: list[SurfaceNode], start: int, end: int) -> None:
    """Stamp a default witness on nodes that don't have one yet.

    Nodes from insertion patterns don't have individual witnesses (the
    insertion dispatcher has many return points).  This catch-all stamps
    them with "fi.insertion_*" based on the node's kind/special fields.
    """
    for i, node in enumerate(nodes):
        if isinstance(node, SurfaceInsertion):
            if node.witness is not None:
                continue
            # Infer rule from insertion shape
            if node.sub_target and node.sub_target.facet == FacetKind.HEADING:
                rid = "fi.insertion_heading"
            elif node.sub_target and node.sub_target.momentti:
                rid = "fi.insertion_sub_target"
            elif node.kind == TargetKind.SECTION:
                rid = "fi.insertion_section"
            elif node.kind == TargetKind.CHAPTER:
                rid = "fi.insertion_chapter"
            else:
                rid = "fi.insertion_other"
            nodes[i] = SurfaceInsertion(
                kind=node.kind,
                label=node.label,
                chapter=node.chapter,
                part=node.part,
                sub_target=node.sub_target,
                witness=_make_witness(rid, start, end),
            )
        elif isinstance(node, SurfaceTargetRef):
            if node.witness is not None:
                continue
            # Infer rule from node shape — must create new node (frozen)
            if node.renumber_dest:
                rid = "fi.section_renumber"
            elif node.sub_refs and node.sub_refs[0].facet == FacetKind.HEADING:
                rid = "fi.insertion_heading"
            elif node.sub_refs and node.sub_refs[0].momentti:
                rid = "fi.insertion_sub_target"
            elif node.kind == TargetKind.SECTION:
                rid = "fi.insertion_section"
            elif node.kind == TargetKind.CHAPTER:
                rid = "fi.insertion_chapter"
            else:
                rid = "fi.insertion_other"
            # Replace the node in the list with a witnessed copy
            nodes[i] = SurfaceTargetRef(
                kind=node.kind,
                label=node.label,
                chapter=node.chapter,
                part=node.part,
                sub_refs=node.sub_refs,
                notes=node.notes,
                renumber_dest=node.renumber_dest,
                renumber_dest_chapter=node.renumber_dest_chapter,
                renumber_dest_part=node.renumber_dest_part,
                witness=_make_witness(rid, start, end),
            )


# ---- Context extraction from surface nodes ----


def _extract_chapter_from_nodes(nodes: list[SurfaceNode], current: str, verb: SourceVerb = SourceVerb.MUUTTAA) -> str:
    """Extract chapter context from surface nodes for propagation.

    The verb parameter controls whether whole-chapter targets propagate:
    only replace (M) and renumber (S) verbs propagate chapter context,
    not repeal (K) or insert (L).
    """
    for node in reversed(nodes):
        if isinstance(node, SurfaceScopeBlock):
            if node.scope_kind == ScopeKind.CHAPTER and node.scope_label:
                return node.scope_label
        elif isinstance(node, SurfaceHeadingPlacement):
            if node.chapter:
                return node.chapter
        elif isinstance(node, SurfaceInsertion):
            if node.chapter:
                return node.chapter
        elif isinstance(node, SurfaceDescendantCoordination):
            if node.base.chapter:
                return node.base.chapter
        elif isinstance(node, SurfaceTargetRef):
            if node.kind == TargetKind.CHAPTER and node.label and _is_whole_target(node):
                # Whole chapter target propagates only for M/S verbs
                if verb not in (SourceVerb.KUMOTA, SourceVerb.LISATA):
                    return node.label
            if node.kind == TargetKind.CHAPTER and node.label and node.sub_refs:
                # Facet-only sub_refs (HEADING/INTRO) still refer to the chapter
                # itself — propagate the chapter label.  Only clear context when
                # sub_refs contain structural descendants (which _chapter_ref
                # never produces, but guard defensively).
                if all(sr.facet and not sr.momentti and not sr.item for sr in node.sub_refs):
                    return node.label
                return ""
            if node.chapter:
                return node.chapter
    return current


def _extract_part_from_nodes(nodes: list[SurfaceNode], current: str) -> str:
    """Extract part context from surface nodes for propagation."""
    # When the batch amends a part's own heading ("II osan ... otsikko"), the
    # part label names a target, not a location scope, so it must not leak
    # forward onto a following arm with a different chapter
    # (cf. "II osan ja 5 luvun otsikko, 15 luvun 2 §").  Detect that case so the
    # heading-chapter branch below knows not to treat its part prefix as scope.
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
        elif isinstance(node, SurfaceHeadingPlacement) and node.part:
            return node.part
        elif isinstance(node, SurfaceInsertion):
            if node.kind == TargetKind.PART and node.label:
                return node.label
            if node.part:
                return node.part
        elif isinstance(node, SurfaceDescendantCoordination) and node.base.part:
            return node.base.part
        elif isinstance(node, SurfaceTargetRef):
            if _is_heading_only_target(node):
                # A part's own heading ("II osan otsikko", kind=PART) is the
                # target being amended, not a scope — it must not leak its label
                # forward (cf. "II osan ja 5 luvun otsikko, 15 luvun 2 §" where
                # the trailing chapter must stay part-less).  But a chapter
                # heading carried under an explicit part prefix
                # ("II osan 4 luvun otsikko") names chapter 4 *located in* part
                # II; that part is a genuine location scope and propagates to a
                # following bare section list ("sekä 38-40 §"), exactly as the
                # chapter label does in _extract_chapter_from_nodes.  This only
                # holds when the batch does not also amend a part heading: when
                # both a part heading and a chapter heading are present the part
                # label is a target, not a scope, so it must not leak forward.
                if (
                    node.kind != TargetKind.PART
                    and node.part
                    and not batch_amends_part_heading
                ):
                    return node.part
                continue
            if node.kind == TargetKind.PART and node.label and not _is_heading_only_target(node):
                return node.label
            if node.part:
                return node.part
    return current


def _normalize_intrabatch_explicit_part_scope(
    nodes: list[SurfaceNode],
    inherited_part: str,
) -> list[SurfaceNode]:
    """Retarget later nodes in one parsed batch when an explicit part appears.

    A single target batch can legally switch parts mid-list, e.g.
    ``V osan 4 luvun numero 25:ksi, VI osan otsikon ..., 1-3 luvun numero
    26-28:ksi``. The parser builds the whole batch before the running part
    context is updated, so the later chapter refs can be stamped with the
    stale inherited part. When an explicit part target appears earlier in the
    same batch, later chapter/section descendants that still carry the old
    inherited part should belong to that new explicit part instead.
    """
    if not nodes:
        return nodes

    active_part = inherited_part
    stale_part_after_explicit_switch = ""
    result: list[SurfaceNode] = []

    for node in nodes:
        explicit_part = ""
        if isinstance(node, SurfaceScopeBlock):
            if node.scope_kind == ScopeKind.PART and node.scope_label:
                explicit_part = node.scope_label
        elif isinstance(node, SurfaceInsertion):
            if node.kind == TargetKind.PART and node.label:
                explicit_part = node.label
        elif isinstance(node, SurfaceTargetRef):
            if node.kind == TargetKind.PART and node.label:
                explicit_part = node.label

        if explicit_part:
            if explicit_part != active_part:
                stale_part_after_explicit_switch = active_part
            active_part = explicit_part
            result.append(node)
            continue

        if (
            active_part
            and stale_part_after_explicit_switch
            and isinstance(node, SurfaceTargetRef)
            and node.kind in {TargetKind.CHAPTER, TargetKind.SECTION}
            and node.part == stale_part_after_explicit_switch
        ):
            # A section's ``chapter`` is contextual scope inherited from before
            # the part switch (e.g. the trailing chapter of a preceding
            # "1-3 luvun" renumber list, or a chapter heading under the prior
            # part).  That chapter belongs to the pre-switch part, so it cannot
            # be carried onto the section once the section is moved to the new
            # ``active_part`` — keeping it produces an internally contradictory
            # address (part from one part, chapter from another).  A bare
            # section is resolved by its number alone, so drop the stale
            # chapter.  Chapter nodes are unaffected: their own ``chapter``
            # field is empty (their ``label`` is the chapter), so switching
            # their part is correct.
            retarget_chapter = node.chapter
            if node.kind == TargetKind.SECTION:
                retarget_chapter = ""
            node = SurfaceTargetRef(
                kind=node.kind,
                label=node.label,
                chapter=retarget_chapter,
                part=active_part,
                sub_refs=node.sub_refs,
                notes=node.notes,
                move_clause_target_unit_kind=node.move_clause_target_unit_kind,
                is_exception=node.is_exception,
                renumber_dest=node.renumber_dest,
                renumber_dest_chapter=node.renumber_dest_chapter,
                renumber_dest_part=node.renumber_dest_part,
                witness=node.witness,
            )
        elif (
            isinstance(node, SurfaceTargetRef)
            and node.part
            and not stale_part_after_explicit_switch
        ):
            active_part = node.part
        result.append(node)

    return result


def _has_doc_ill_in_range(s: Stream, start: int, end: int) -> bool:
    """Check if any DOC:ILL token (asetukseen/lakiin) exists in [start, end)."""
    for i in range(start, min(end, len(s.tokens))):
        if s.tokens[i].cat == "DOC" and s.tokens[i].case == "ILL":
            return True
    return False


# ---- Inline move tail handling ----
# These work on surface nodes rather than ParsedOps, applying
# SurfaceMoveTail to the nodes list.


def _tag_inline_move_clause_target_batch(
    all_nodes: list[SurfaceNode],
    batch_nodes: list[SurfaceNode],
    *,
    dest_chapter: str = "",
    dest_part: str = "",
) -> None:
    """Attach inline move-tail destination scope to the immediately preceding batch.

    Mutates the all_nodes list by replacing affected SurfaceTargetRef nodes
    with copies that have chapter/part set.
    """
    if not dest_chapter and not dest_part:
        return

    # Find labels of whole-section targets in the batch, plus their source
    # chapter/part context so we only retag the intended batch lineage.
    moved_labels: set[str] = set()
    batch_chapters: set[str] = set()
    batch_parts: set[str] = set()
    for node in batch_nodes:
        if isinstance(node, SurfaceTargetRef) and node.kind == TargetKind.SECTION:
            if _is_whole_target(node):
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
        if not _is_whole_target(node):
            continue
        if node.chapter and batch_chapters and node.chapter not in batch_chapters and node.chapter != dest_chapter:
            continue
        if node.part and batch_parts and node.part not in batch_parts and node.part != dest_part:
            continue

        new_chapter = dest_chapter if dest_chapter else node.chapter
        new_part = dest_part if dest_part and not node.part else node.part
        all_nodes[i] = SurfaceTargetRef(
            kind=node.kind,
            label=node.label,
            chapter=new_chapter,
            part=new_part,
            sub_refs=node.sub_refs,
            notes=node.notes,
            move_clause_target_unit_kind="chapter" if dest_chapter else "part" if dest_part else None,
            renumber_dest=node.renumber_dest,
            renumber_dest_chapter=node.renumber_dest_chapter,
            renumber_dest_part=node.renumber_dest_part,
            witness=node.witness,
        )


def _inline_move_clause_tail_destination(
    s: Stream,
    all_nodes: list[SurfaceNode] | None = None,
    batch_nodes: list[SurfaceNode] | None = None,
) -> tuple[str, str] | None:
    """Consume an inline move tail and return its destination carrier."""
    saved = s.save()

    if (t := s.peek()) and t.cat == "CONJ":
        s.pos += 1

    if (t := s.peek()) and t.cat == "COMMA":
        s.pos += 1

    while (t := s.peek()) and t.cat == "WORD":
        s.pos += 1

    t = s.peek()
    if not (t and t.cat == "VERB" and t.verb_code == SourceVerb.SIIRTAA):
        s.restore(saved)
        return None
    s.pos += 1

    while (t := s.peek()) and (
        t.cat == "STATUTE_NAME_SPAN"
        or (
            t.cat == "WORD"
            and (t.lemma in {"se", "ne"} or t.text.lower() in {"se", "ne"})
        )
    ):
        s.pos += 1

    if (t := s.peek()) and t.cat == "DOC" and t.case == "ILL":
        s.pos += 1
        while (t := s.peek()) and t.cat == "WORD":
            s.pos += 1

    nums = _number_list(s)
    if not nums or len(nums) != 1:
        s.restore(saved)
        return None
    t = s.peek()
    if not t:
        s.restore(saved)
        return None
    dest_label = nums[0][0] + nums[0][1]
    if t.cat == "LUKU" and t.case == "ILL":
        s.pos += 1
        if all_nodes is not None and batch_nodes is not None:
            _tag_inline_move_clause_target_batch(all_nodes, batch_nodes, dest_chapter=dest_label)
        return (dest_label, "")
    if t.cat == "OSA" and t.case == "ILL":
        s.pos += 1
        if all_nodes is not None and batch_nodes is not None:
            _tag_inline_move_clause_target_batch(all_nodes, batch_nodes, dest_part=dest_label)
        return ("", dest_label)
    s.restore(saved)
    return None


def _skip_inline_move_clause_tail(
    s: Stream,
    all_nodes: list[SurfaceNode] | None = None,
    batch_nodes: list[SurfaceNode] | None = None,
) -> bool:
    """Consume inline move tails and tag the preceding target batch natively."""
    return _inline_move_clause_tail_destination(s, all_nodes, batch_nodes) is not None


def _leading_move_destination_part(s: Stream) -> str:
    """Consume a leading destination-part prefix on a move verb group."""
    saved = s.save()
    nums = _number_list(s)
    if not nums or len(nums) != 1:
        return ""
    t = s.peek()
    if not (t and t.cat == "OSA" and t.case == "ILL"):
        s.restore(saved)
        return ""
    s.pos += 1
    if (t := s.peek()) and t.cat == "COMMA":
        s.pos += 1
    return nums[0][0] + nums[0][1]


def _leading_move_destination_chapter(s: Stream) -> str:
    """Consume a leading destination-chapter prefix on a move verb group."""
    saved = s.save()

    if (t := s.peek()) and t.cat == "DOC" and t.case == "ILL":
        s.pos += 1

    if not _uusi(s):
        s.restore(saved)
        return ""

    nums = _number_list(s)
    if not nums or len(nums) != 1:
        s.restore(saved)
        return ""

    t = s.peek()
    if not (t and t.cat == "LUKU" and t.case != "ILL"):
        s.restore(saved)
        return ""
    s.pos += 1

    if (t := s.peek()) and t.cat == "COMMA":
        s.pos += 1

    saw_johon = False
    saw_samalla = False
    while (t := s.peek()) and t.cat == "WORD":
        txt = t.text.lower()
        if txt == "johon":
            saw_johon = True
        elif txt == "samalla":
            saw_samalla = True
        s.pos += 1

    t = s.peek()
    if not (saw_johon and saw_samalla and t and t.cat == "VERB" and t.verb_code == SourceVerb.SIIRTAA):
        s.restore(saved)
        return ""
    s.pos += 1

    return nums[0][0] + nums[0][1]


def _skip_heading_residue(
    s: Stream, *, allow_section_destination_residue: bool = False
) -> bool:
    """Consume a bare heading-placement residue left after provenance tagging."""
    saved = s.save()
    if not ((t := s.peek()) and t.cat == "UUSI"):
        return False
    s.pos += 1  # consume UUSI
    t = s.peek()
    if t and t.cat == "OTSIKKO":
        s.pos += 1
        return True
    if t and t.cat == "LUKU" and t.case == "GEN":
        s.pos += 1
        if (t := s.peek()) and t.cat == "OTSIKKO":
            s.pos += 1
            return True
        s.restore(saved)
        return False
    if t and t.cat == "NUM":
        s.pos += 1  # consume NUM
        if (t := s.peek()) and t.cat == "LETTER":
            s.pos += 1
        if (
            allow_section_destination_residue
            and (t := s.peek())
            and t.cat == "PYKALA"
            and t.case == "GEN"
        ):
            s.pos += 1
            if (t := s.peek()) and t.cat == "EDELLA":
                s.pos += 1
                return True
            s.restore(saved)
            return False
        if (t := s.peek()) and t.cat == "LUKU" and t.case == "GEN":
            s.pos += 1
            if (t := s.peek()) and t.cat == "OTSIKKO":
                s.pos += 1
                return True
    s.restore(saved)
    return False


def _consume_including_preceding_heading_target(
    s: Stream,
    chapter: str,
    *,
    part: str = "",
) -> Optional[list[SurfaceNode]]:
    """Consume ``mukaanluettuna N §:n edellä olevan väliotsikon`` as a heading target.

    Historical Finnish amendment formulae can attach a named preceding-heading
    facet to an earlier section-range arm without using the anaphoric
    ``sen/pykälän edellä olevan väliotsikon`` shape. We must preserve that
    explicit section ownership and then continue parsing later target arms.
    """
    saved = s.save()
    if not ((t := s.peek()) and t.cat == "WORD" and t.text.lower() == "mukaanluettuna"):
        return None
    s.pos += 1

    nums = _number_list(s)
    if not nums:
        s.restore(saved)
        return None

    if not ((t := s.peek()) and t.cat == "PYKALA" and t.case == "GEN"):
        s.restore(saved)
        return None
    s.pos += 1

    if not ((t := s.peek()) and t.cat == "EDELLA"):
        s.restore(saved)
        return None
    s.pos += 1

    if not ((t := s.peek()) and (t.lemma or "").lower() == "olla"):
        s.restore(saved)
        return None
    s.pos += 1

    if not ((t := s.peek()) and t.cat == "OTSIKKO"):
        s.restore(saved)
        return None
    s.pos += 1

    witness = _make_witness("fi.including_preceding_heading_target", saved, s.pos)
    nodes: list[SurfaceNode] = []
    for n, sf in nums:
        expanded = _expand_range_single(n)
        for rn in expanded:
            full = rn + (sf if len(expanded) == 1 else "")
            nodes.append(
                SurfaceTargetRef(
                    kind=TargetKind.SECTION,
                    label=full,
                    chapter=chapter,
                    part=part,
                    sub_refs=(SurfaceSubRef(facet=FacetKind.HEADING),),
                    witness=witness,
                )
            )
    return nodes


def _skip_named_row_residue(s: Stream) -> bool:
    """Consume a post-target named-row residue like ``koodi 121``.

    The structural target parser intentionally stops before the row-designator
    tail. Mixed clauses can then continue with additional ordinary targets
    after provenance tagging, so this residue must not block `_target_list()`
    from reaching the next explicit target arm.
    """
    saved = s.save()
    t = s.peek()
    if not (t and t.cat == "WORD" and t.text.lower() == "koodi"):
        return False
    s.pos += 1

    saw_code = False
    while (t := s.peek()) and t.cat in ("NUM", "LETTER", "DASH"):
        s.pos += 1
        saw_code = True

    if saw_code:
        return True

    s.restore(saved)
    return False


_PROV_ANAPHOR_WORDS = frozenset({"näistä", "niistä"})

# Anaphoric backref determiners that point at the last-mentioned structural unit
# for an *insert* target, e.g. "sanottuun pykälään uusi 5 momentti" / "mainittuun
# momenttiin uusi 3 kohta" / "saman pykälän 2 momenttiin uusi 4 kohta" /
# "sanottuun lakiin uusi 11 a §".  These lex as bare WORD tokens (no dedicated
# BACKREF category) and immediately precede a structural noun.
_ANAPHORIC_INSERT_DETERMINERS = frozenset(
    {
        "sanottuun",
        "sanottu",
        "sanottua",
        "mainittuun",
        "mainittua",
        "samaan",
        "saman",
        "tähän",
        "tuohon",
    }
)


def _skip_provenance_anaphor_backref(s: Stream) -> bool:
    """Consume a ``näistä/niistä <ref> sellaisena kuin ...`` provenance back-ref.

    Finnish amendment preambles attribute the source version of *already
    listed* targets with an anaphoric appositive::

        ... 5 §:n 1, 2 ja 4 momentti, näistä 5 §:n 4 momentti sellaisena
        kuin se on laissa 1017/1994, seuraavasti:

    Here ``näistä 5 §:n 4 momentti`` ("of these, the 4th subsection of §5")
    is a back-reference to a previously enumerated target, NOT a fresh target.
    Without this skip the section reference following the anaphor is re-parsed
    as a duplicate op, over-claiming the amendment.

    The anaphor is only consumed when it is followed by a structural reference
    AND that reference is closed by a provenance trigger (PROV span or the
    CITATION_SPAN that absorbs ``sellaisena kuin ... laissa NNN/YYYY``).  That
    closing trigger is what marks the run as an attribution clause rather than
    a genuine continuation of the target list.
    """
    saved = s.save()
    t = s.peek()
    if not (t and t.cat == "WORD" and t.text.lower() in _PROV_ANAPHOR_WORDS):
        return False
    s.pos += 1

    saw_structural = False
    while (t := s.peek()) and t.cat in (
        "NUM",
        "LETTER",
        "DASH",
        "CONJ",
        "COMMA",
        "PYKALA",
        "MOMENTTI",
        "KOHTA",
        "ALAKOHTA",
        "JOHD",
        "OTSIKKO",
    ):
        if t.cat in ("PYKALA", "MOMENTTI", "KOHTA"):
            saw_structural = True
        s.pos += 1

    # Require the run to be closed by a provenance trigger.  Without it the
    # anaphor would be a malformed fragment; bail out and leave the stream
    # untouched so normal target parsing can retry.
    if saw_structural and (t := s.peek()) and t.cat in ("PROV", "CITATION_SPAN"):
        if t.cat == "PROV":
            s.pos = _skip_prov_span(s.tokens, s.pos, len(s.tokens))
        else:
            s.skip_sentinels()
        return True

    s.restore(saved)
    return False


def _skip_named_heading_anchor_before_insert(s: Stream) -> bool:
    """Consume a named heading anchor before a doc-level insert target.

    Real source family: "asetukseen apteekkeja, sivuapteekkeja ja
    lääkekaappeja koskevan väliotsikon edelle uusi 10 b-10 f §".

    This is not a heading insertion. It is a section insertion whose placement
    is anchored before a named heading. We only consume the anchor when it is
    structurally complete and immediately followed by ``uusi``.
    """
    saved = s.save()
    saw_heading = False
    while (t := s.peek()) and t.cat in {"WORD", "COMMA", "CONJ", "OTSIKKO"}:
        if t.cat == "OTSIKKO":
            saw_heading = True
        s.pos += 1
    if not saw_heading:
        s.restore(saved)
        return False
    if not ((t := s.peek()) and t.cat == "EDELLA"):
        s.restore(saved)
        return False
    s.pos += 1
    if not ((t := s.peek()) and t.cat == "UUSI"):
        s.restore(saved)
        return False
    return True


def _lukuun_ottamatta_exception(
    s: Stream,
    verb: SourceVerb,
    chapter: str,
    part: str = "",
) -> Optional[list[SurfaceNode]]:
    """Parse 'lukuun ottamatta (kuitenkaan)? <section_ref>' exception clause.

    Finnish amendment clauses use "lukuun ottamatta (kuitenkaan)?" to exclude
    specific sections from a broader range.  Example:

        muutetaan 4-7 luku, lukuun ottamatta kuitenkaan 7 luvun 73 §:ää

    The excepted section is still an operative target within the same verb
    group — it's just semantically excluded from the chapter range.  For
    downstream relabel processing, the excepted section becomes the most
    recent section in context so that "joka siirretään" can bind to it.

    Token pattern:
        LUKU:ILL  WORD("ottamatta")  [WORD("kuitenkaan")]?  <section_ref>

    Returns parsed section nodes on success, None on failure (restores pos).
    """
    saved = s.save()
    t = s.peek()
    if not (t and t.cat == "LUKU" and t.case == "ILL"):
        return None
    s.pos += 1  # consume "lukuun"
    t = s.peek()
    if not (t and t.cat == "WORD" and t.text.lower() == "ottamatta"):
        s.restore(saved)
        return None
    s.pos += 1  # consume "ottamatta"
    # Optional "kuitenkaan"
    if (t := s.peek()) and t.cat == "WORD" and t.text.lower() == "kuitenkaan":
        s.pos += 1
    # Parse the excepted section reference
    nodes = _section_ref(s, verb, chapter, part=part)
    if not nodes:
        s.restore(saved)
        return None
    # Re-stamp witness, set is_exception=True, and keep "exception" note for
    # backward compatibility.  The typed is_exception field is the authority.
    _w = _make_witness("fi.lukuun_ottamatta_exception", saved, s.pos)
    for i, node in enumerate(nodes):
        if isinstance(node, SurfaceTargetRef):
            nodes[i] = SurfaceTargetRef(
                kind=node.kind,
                label=node.label,
                chapter=node.chapter,
                part=node.part,
                sub_refs=node.sub_refs,
                notes=node.notes + ("exception",),
                is_exception=True,
                renumber_dest=node.renumber_dest,
                renumber_dest_chapter=node.renumber_dest_chapter,
                renumber_dest_part=node.renumber_dest_part,
                witness=_w,
            )
        elif isinstance(node, SurfaceScopeBlock):
            # Section refs with an explicit chapter prefix (e.g. "7 luvun 73 §")
            # are wrapped in a SurfaceScopeBlock — stamp is_exception on each
            # enclosed SurfaceTargetRef.
            new_targets: list[SurfaceTargetRef] = []
            for t in node.targets:
                if not isinstance(t, SurfaceTargetRef):
                    continue
                new_targets.append(
                    SurfaceTargetRef(
                        kind=t.kind,
                        label=t.label,
                        chapter=t.chapter,
                        part=t.part,
                        sub_refs=t.sub_refs,
                        notes=t.notes + ("exception",),
                        is_exception=True,
                        renumber_dest=t.renumber_dest,
                        renumber_dest_chapter=t.renumber_dest_chapter,
                        renumber_dest_part=t.renumber_dest_part,
                        witness=_w,
                    )
                )
            nodes[i] = SurfaceScopeBlock(
                scope_kind=node.scope_kind,  # already a ScopeKind enum
                scope_label=node.scope_label,
                targets=tuple(new_targets),
                witness=_w,
            )
        elif isinstance(node, SurfaceDescendantCoordination):
            new_base = SurfaceTargetRef(
                kind=node.base.kind,
                label=node.base.label,
                chapter=node.base.chapter,
                part=node.base.part,
                sub_refs=node.base.sub_refs,
                notes=node.base.notes + ("exception",),
                is_exception=True,
                renumber_dest=node.base.renumber_dest,
                renumber_dest_chapter=node.base.renumber_dest_chapter,
                renumber_dest_part=node.base.renumber_dest_part,
                witness=_w,
            )
            nodes[i] = SurfaceDescendantCoordination(
                base=new_base,
                arms=node.arms,
                witness=_w,
            )
    return nodes


def _parse_anaphoric_determiner_insert(
    s: Stream,
    verb: SourceVerb,
    chapter: str,
    part: str,
    all_nodes: list[SurfaceNode],
) -> Optional[list[SurfaceNode]]:
    """Resolve an anaphoric-determiner insert arm against the last target.

    Handles continuation arms whose target is named only by an anaphoric
    determiner pointing at a previously mentioned structural unit::

        ... lisätään 8 §:n 3 momenttiin uusi 10 kohta ja
            *sanottuun pykälään* uusi 5 ja 6 momentti sekä lakiin uusi 11 a §
        ... saman pykälän 2 momenttiin uusi 4 kohta
        ... mainittuun momenttiin uusi 5 kohta
        ... sanottuun lakiin uusi 3 §

    The determiner (``sanottuun``/``mainittuun``/``saman``/``samaan``...) lexes
    as a bare ``WORD`` immediately before the structural noun.  Without this
    branch the continuation loop in :func:`_target_list` cannot advance past the
    determiner, so it aborts and silently drops every downstream arm.

    Returns the resolved insertion nodes on success (stream advanced past the
    arm), or ``None`` with the stream restored.
    """
    if verb != SourceVerb.LISATA:
        return None
    t = s.peek()
    if not (t and t.cat == "WORD" and t.text.lower() in _ANAPHORIC_INSERT_DETERMINERS):
        return None
    nxt = s.tokens[s.pos + 1] if s.pos + 1 < len(s.tokens) else None
    if nxt is None or nxt.cat not in ("PYKALA", "MOMENTTI", "DOC", "LUKU", "OSA"):
        return None

    saved = s.save()
    ctx = _extract_section_context_from_nodes(
        all_nodes, VerbGroupContext(chapter=chapter), verb
    )
    prev_sec = ctx.last_section
    prev_ch = ctx.last_section_chapter or chapter
    prev_mom = ctx.last_momentti

    s.pos += 1  # consume the determiner WORD

    # "sanottuun/mainittuun/samaan lakiin uusi N §|liite|luku" — law-level (root)
    # insert: hand the remaining stream to the normal DOC:ILL insertion parser by
    # leaving the structural noun in place and dispatching through _insertion.
    if nxt.cat in ("DOC", "OSA"):
        ins = _insertion(s, verb, chapter, part=part)
        if ins:
            return ins
        s.restore(saved)
        return None

    # "saman lain N lukuun uusi M §" — chapter-scoped: let _insertion handle the
    # chapter context once the determiner is consumed.
    if nxt.cat == "LUKU":
        ins = _insertion(s, verb, chapter, part=part)
        if ins:
            return ins
        s.restore(saved)
        return None

    if not prev_sec:
        s.restore(saved)
        return None

    # "sanottuun/samaan pykälään uusi N momentti/kohta" — section anaphora.
    if nxt.cat == "PYKALA" and nxt.case == "ILL":
        s.pos += 1  # consume PYKALA:ILL
        if (tc := s.peek()) and tc.cat == "COMMA":
            s.pos += 1
        s.skip_cats(_TILALLE_OR_REINST | frozenset({"PROVENANCE_SPAN"}))
        if (tc := s.peek()) and tc.cat == "COMMA":
            s.pos += 1
        if _uusi(s):
            nodes = _insertion_sub_target(s, verb, prev_sec, prev_ch, "", 0)
            if nodes:
                return nodes
        s.restore(saved)
        return None

    # "saman pykälän M momenttiin uusi K kohta" — section genitive then momentti.
    if nxt.cat == "PYKALA" and nxt.case == "GEN":
        s.pos += 1  # consume PYKALA:GEN
        mom_nums = _number_list(s)
        if mom_nums and (tm := s.peek()) and tm.cat == "MOMENTTI" and tm.case == "ILL":
            mom_num = int(mom_nums[0][0]) if mom_nums[0][0].isdigit() else 0
            s.pos += 1  # consume MOMENTTI:ILL
            if (tc := s.peek()) and tc.cat == "COMMA":
                s.pos += 1
            if _uusi(s):
                nodes = _insertion_sub_target(s, verb, prev_sec, prev_ch, "", mom_num)
                if nodes:
                    return nodes
        s.restore(saved)
        return None

    # "mainittuun/samaan momenttiin uusi K kohta" — momentti anaphora.
    if nxt.cat == "MOMENTTI" and nxt.case == "ILL":
        if not prev_mom:
            s.restore(saved)
            return None
        s.pos += 1  # consume MOMENTTI:ILL
        if (tc := s.peek()) and tc.cat == "COMMA":
            s.pos += 1
        if _uusi(s):
            nodes = _insertion_sub_target(s, verb, prev_sec, prev_ch, "", prev_mom)
            if nodes:
                return nodes
        s.restore(saved)
        return None

    s.restore(saved)
    return None


def _current_section_renumber_tail(
    s: Stream,
    verb: SourceVerb,
    chapter: str,
    part: str = "",
) -> Optional[list[SurfaceNode]]:
    """Parse SIIRTAA ``nykyinen N § uudeksi M §:ksi`` section relabel tails."""
    if verb != SourceVerb.SIIRTAA:
        return None
    saved = s.save()
    start = s.pos

    current_word = s.peek()
    if not (
        current_word
        and current_word.cat == "WORD"
        and (current_word.lemma or current_word.text).lower() in {"nykyinen", "nykyiset"}
    ):
        return None
    s.pos += 1

    source_labels = _number_list(s)
    if not source_labels:
        s.restore(saved)
        return None
    if not ((source_pykala := s.peek()) and source_pykala.cat == "PYKALA"):
        s.restore(saved)
        return None
    s.pos += 1

    new_word = s.peek()
    if not (
        new_word
        and new_word.cat == "WORD"
        and (new_word.lemma or new_word.text).lower() in {"uudeksi", "uusiksi"}
    ):
        s.restore(saved)
        return None
    s.pos += 1

    destination_labels = _number_list(s)
    if not destination_labels:
        s.restore(saved)
        return None
    if not ((dest_pykala := s.peek()) and dest_pykala.cat == "PYKALA"):
        s.restore(saved)
        return None
    s.pos += 1

    if len(source_labels) != len(destination_labels):
        s.restore(saved)
        return None

    witness = _make_witness("fi.current_section_renumber_tail", start, s.pos)
    nodes: list[SurfaceNode] = []
    for source_label, destination_label in zip(source_labels, destination_labels, strict=True):
        nodes.append(
            SurfaceTargetRef(
                kind=TargetKind.SECTION,
                label=source_label[0] + source_label[1],
                chapter=chapter,
                part=part,
                notes=("renumber_clause",),
                witness=witness,
            )
        )
        nodes.append(
            SurfaceRenumberTail(
                new_label=destination_label[0] + destination_label[1],
                witness=witness,
            )
        )
    return nodes


def _target_list(
    s: Stream,
    verb: SourceVerb,
    chapter: str,
    *,
    started_with_citation_span_hint: bool = False,
) -> list[SurfaceNode]:
    """Parse a list of targets for one verb. Returns SurfaceNode list."""
    part = ""
    # Skip sentinel span tokens before first target
    leading_target_start = s.pos
    s.skip_sentinels()
    started_with_citation_span = (
        started_with_citation_span_hint
        or (
            leading_target_start < len(s.tokens)
            and s.pos > leading_target_start
            and s.tokens[leading_target_start].cat == "CITATION_SPAN"
        )
    )
    _pre = s.pos
    nodes = _target(
        s,
        verb,
        chapter,
        part=part,
        started_with_citation_span_hint=started_with_citation_span,
    )
    if not nodes:
        # Initial-position backref targets: a new verb group can begin with
        # "mainitun luvun ..." / "mainitun osan ..." and must be parsed as a
        # fresh scoped target batch, not treated as residue waiting for a
        # prior in-list separator.
        saved_backref = s.save()
        part_nodes = _parse_part_backref_target(s, verb, chapter, part)
        if part_nodes:
            nodes = part_nodes
        else:
            s.restore(saved_backref)
            chapter_nodes = _parse_chapter_backref_target(s, verb, chapter, part)
            if chapter_nodes:
                nodes = chapter_nodes
            else:
                s.restore(saved_backref)
        if not nodes:
            renumber_tail_nodes = _current_section_renumber_tail(s, verb, chapter, part)
            if renumber_tail_nodes:
                nodes = renumber_tail_nodes
        # Try skipping a leading heading insertion
        _leading_heading = seq(
            _uusi,
            cat("OTSIKKO"),
        )
        _leading_heading_luku = seq(
            _uusi,
            cat_case("LUKU", "GEN"),
            cat("OTSIKKO"),
        )
        saved_lh = s.save()
        if _leading_heading(s) or _leading_heading_luku(s):
            while not s.at_end():
                _pt = s.peek()
                if _pt is None:
                    break
                if _pt.cat in ("VERB", "END", "END_SENTINEL_SPAN", "DOC"):
                    break
                if _pt.cat == "EDELLA":
                    s.pos += 1
                    break
                s.pos += 1
            _sep(s)
            nodes = _target(s, verb, chapter, part=part)
        if not nodes:
            s.restore(saved_lh)
            # Fallback: skip a leading CONJ separator (sekä/ja) before targets.
            # This handles rare patterns like "lisätään sekä II A osa" where
            # a conjunction appears immediately after the verb.
            saved_conj = s.save()
            if _sep(s) is not None:
                nodes = _target(s, verb, chapter, part=part)
            if not nodes:
                s.restore(saved_conj)
                return []

    nodes = _normalize_intrabatch_explicit_part_scope(nodes, part)
    all_nodes: list[SurfaceNode] = list(nodes)
    last_batch_nodes = list(nodes)
    allow_named_row_continuation = _skip_named_row_residue(s)
    if allow_named_row_continuation:
        s.skip_sentinels()
    # DOC:ILL resets chapter scope
    if _has_doc_ill_in_range(s, _pre, s.pos):
        chapter = ""
    else:
        chapter = _extract_chapter_from_nodes(nodes, chapter, verb)
    part = _extract_part_from_nodes(nodes, part)
    while _skip_inline_move_clause_tail(s, all_nodes, last_batch_nodes):
        pass

    while True:
        saved = s.save()
        if s.at_end():
            break
        if (t := s.peek()) and t.cat == "VERB":
            break
        if _sep(s) is None:
            nodes = _consume_including_preceding_heading_target(s, chapter, part=part)
            if nodes:
                nodes = _normalize_intrabatch_explicit_part_scope(nodes, part)
                all_nodes.extend(nodes)
                last_batch_nodes = list(nodes)
                chapter = _extract_chapter_from_nodes(nodes, chapter, verb)
                part = _extract_part_from_nodes(nodes, part)
                continue
            if _skip_heading_residue(
                s, allow_section_destination_residue=verb == SourceVerb.SIIRTAA
            ):
                continue
            if allow_named_row_continuation and s.pos == saved:
                _pre_span = s.pos
                nodes = _target(s, verb, chapter, part=part)
                allow_named_row_continuation = False
                if nodes:
                    nodes = _normalize_intrabatch_explicit_part_scope(nodes, part)
                    all_nodes.extend(nodes)
                    last_batch_nodes = list(nodes)
                    allow_named_row_continuation = _skip_named_row_residue(s)
                    if allow_named_row_continuation:
                        s.skip_sentinels()
                    if _has_doc_ill_in_range(s, _pre_span, s.pos):
                        chapter = ""
                    else:
                        chapter = _extract_chapter_from_nodes(nodes, chapter, verb)
                    part = _extract_part_from_nodes(nodes, part)
                    while _skip_inline_move_clause_tail(s, all_nodes, last_batch_nodes):
                        pass
                    continue
            # Skip orphaned UUSI body marker immediately before a separator.
            # annotate_qualifiers can remove a sub-target qualifier (e.g. "c alakohta")
            # leaving "uusi" with no parseable body content right before a COMMA or CONJ.
            if verb == SourceVerb.LISATA:
                _orphan_t = s.peek()
                _sep_t = s.tokens[s.pos + 1] if s.pos + 1 < len(s.tokens) else None
                if (
                    _orphan_t and _orphan_t.cat == "UUSI"
                    and _sep_t and _sep_t.cat in ("COMMA", "CONJ")
                ):
                    s.pos += 1  # skip the orphaned UUSI; outer loop will consume COMMA/CONJ
                    continue
            if s.pos != saved:
                batch_nodes = cast(list[SurfaceNode] | None, last_batch_nodes)
                if _skip_inline_move_clause_tail(s, all_nodes, batch_nodes):
                    while _skip_inline_move_clause_tail(s, all_nodes, batch_nodes):
                        pass
                    continue
                if _skip_named_row_residue(s):
                    s.skip_sentinels()
                    continue
                if _skip_heading_residue(
                    s, allow_section_destination_residue=verb == SourceVerb.SIIRTAA
                ):
                    continue
                _pre_span = s.pos
                nodes = _target(s, verb, chapter, part=part)
                if nodes:
                    nodes = _normalize_intrabatch_explicit_part_scope(nodes, part)
                    all_nodes.extend(nodes)
                    last_batch_nodes = list(nodes)
                    if _has_doc_ill_in_range(s, _pre_span, s.pos):
                        chapter = ""
                    else:
                        chapter = _extract_chapter_from_nodes(nodes, chapter, verb)
                    part = _extract_part_from_nodes(nodes, part)
                    while _skip_inline_move_clause_tail(s, all_nodes, last_batch_nodes):
                        pass
                    continue
            break
        if s.at_end() or ((t := s.peek()) and t.cat == "VERB"):
            s.restore(saved)
            break
        # "näistä/niistä <ref> sellaisena kuin ..." after a separator is a
        # provenance back-reference to an already-listed target, not a new one.
        if _skip_provenance_anaphor_backref(s):
            continue
        _pre_t = s.pos
        nodes = _target(s, verb, chapter, part=part)
        if nodes:
            nodes = _normalize_intrabatch_explicit_part_scope(nodes, part)
            all_nodes.extend(nodes)
            last_batch_nodes = list(nodes)
            allow_named_row_continuation = _skip_named_row_residue(s)
            if allow_named_row_continuation:
                s.skip_sentinels()
            if _has_doc_ill_in_range(s, _pre_t, s.pos):
                chapter = ""
            else:
                chapter = _extract_chapter_from_nodes(nodes, chapter, verb)
            part = _extract_part_from_nodes(nodes, part)
            while _skip_inline_move_clause_tail(s, all_nodes, last_batch_nodes):
                pass
            continue
        if not nodes:
            saved_after_target_attempt = s.save()
            s.restore(_pre_t)
            renumber_tail_nodes = _current_section_renumber_tail(s, verb, chapter, part)
            if renumber_tail_nodes:
                renumber_tail_nodes = _normalize_intrabatch_explicit_part_scope(
                    renumber_tail_nodes, part
                )
                all_nodes.extend(renumber_tail_nodes)
                last_batch_nodes = list(renumber_tail_nodes)
                chapter = _extract_chapter_from_nodes(renumber_tail_nodes, chapter, verb)
                part = _extract_part_from_nodes(renumber_tail_nodes, part)
                while _skip_inline_move_clause_tail(s, all_nodes, last_batch_nodes):
                    pass
                continue
            s.restore(saved_after_target_attempt)
            # Anaphoric determiner insert arm: "sanottuun/mainittuun/samaan
            # pykälään|momenttiin uusi ..." / "saman pykälän N momenttiin uusi
            # ..." / "sanottuun lakiin uusi N §".  Resolved against the last
            # mentioned section/momentti so the continuation loop does not abort
            # and drop downstream arms.
            det_nodes = _parse_anaphoric_determiner_insert(
                s, verb, chapter, part, all_nodes
            )
            if det_nodes:
                det_nodes = _normalize_intrabatch_explicit_part_scope(det_nodes, part)
                _w_det = _make_witness("fi.anaphoric_determiner_insert", saved, s.pos)
                for _di, _dn in enumerate(det_nodes):
                    if isinstance(_dn, SurfaceInsertion) and _dn.witness is None:
                        det_nodes[_di] = SurfaceInsertion(
                            kind=_dn.kind,
                            label=_dn.label,
                            chapter=_dn.chapter,
                            part=_dn.part,
                            sub_target=_dn.sub_target,
                            witness=_w_det,
                        )
                all_nodes.extend(det_nodes)
                last_batch_nodes = list(det_nodes)
                chapter = _extract_chapter_from_nodes(det_nodes, chapter, verb)
                part = _extract_part_from_nodes(det_nodes, part)
                while _skip_inline_move_clause_tail(s, all_nodes, last_batch_nodes):
                    pass
                continue

            # Anaphoric chapter-gen carry-forward: after a prior chapter-scoped
            # target, later arms can start with bare "luvun ..." instead of
            # repeating the chapter number. Consume the genitive marker and
            # reparse the remainder against the inherited chapter context.
            t2 = s.peek()
            if t2 and t2.cat == "LUKU" and t2.case == "GEN" and chapter:
                saved_ch_backref = s.save()
                s.pos += 1
                nodes = _target(s, verb, chapter, part=part)
                if nodes:
                    nodes = _normalize_intrabatch_explicit_part_scope(nodes, part)
                    all_nodes.extend(nodes)
                    last_batch_nodes = list(nodes)
                    chapter = _extract_chapter_from_nodes(nodes, chapter, verb)
                    part = _extract_part_from_nodes(nodes, part)
                    while _skip_inline_move_clause_tail(s, all_nodes, last_batch_nodes):
                        pass
                    continue
                s.restore(saved_ch_backref)

            # Try "lukuun ottamatta (kuitenkaan)?" exception clause
            exc_nodes = _lukuun_ottamatta_exception(s, verb, chapter, part=part)
            if exc_nodes:
                all_nodes.extend(exc_nodes)
                last_batch_nodes = list(exc_nodes)
                chapter = _extract_chapter_from_nodes(exc_nodes, chapter, verb)
                part = _extract_part_from_nodes(exc_nodes, part)
                while _skip_inline_move_clause_tail(s, all_nodes, last_batch_nodes):
                    pass
                continue

            # Try back-reference: "mainitun pykälän [sub_ref]"
            t2 = s.peek()
            if t2 and t2.cat == "BACKREF":
                is_singular = t2.text.lower() in ("mainitun", "mainittu")
                br_subs = _parse_backref_continuation(s)
                if br_subs is not None:
                    # Emit SurfaceBackRef
                    referent_type = BackRefArity.SINGULAR if is_singular else BackRefArity.PLURAL
                    all_nodes.append(
                        SurfaceBackRef(
                            referent_type=referent_type,
                            sub_refs=_to_surface_sub_refs(br_subs),
                            witness=_make_witness(
                                "fi.backref_singular" if is_singular else "fi.backref_plural",
                                saved,
                                s.pos,
                            ),
                        )
                    )
                    continue

                part_nodes = _parse_part_backref_target(s, verb, chapter, part)
                if part_nodes:
                    all_nodes.extend(part_nodes)
                    last_batch_nodes = list(part_nodes)
                    chapter = _extract_chapter_from_nodes(part_nodes, chapter, verb)
                    part = _extract_part_from_nodes(part_nodes, part)
                    while _skip_inline_move_clause_tail(s, all_nodes, last_batch_nodes):
                        pass
                    continue

                chapter_nodes = _parse_chapter_backref_target(s, verb, chapter, part)
                if chapter_nodes:
                    all_nodes.extend(chapter_nodes)
                    last_batch_nodes = list(chapter_nodes)
                    chapter = _extract_chapter_from_nodes(chapter_nodes, chapter, verb)
                    part = _extract_part_from_nodes(chapter_nodes, part)
                    while _skip_inline_move_clause_tail(s, all_nodes, last_batch_nodes):
                        pass
                    continue

            # Valiotsikko heading back-reference: "sen edellä oleva väliotsikko"
            if t2 and t2.cat == "VALIOTSIKKO":
                s.pos += 1
                all_nodes.append(
                    SurfaceValiotsikkoRef(
                        witness=_make_witness("fi.valiotsikko_heading_ref", saved, s.pos),
                    )
                )
                continue

            # Try anaphoric "pykälään uusi N momentti/kohta"
            t2 = s.peek()
            if t2 and t2.cat == "PYKALA" and t2.case == "ILL":
                prev_sec = ""
                prev_ch = chapter
                for prev_node in reversed(all_nodes):
                    if (
                        isinstance(prev_node, SurfaceInsertion)
                        and prev_node.kind == TargetKind.SECTION
                        and prev_node.label
                    ):
                        prev_sec = prev_node.label
                        if prev_node.chapter:
                            prev_ch = prev_node.chapter
                        break
                    if (
                        isinstance(prev_node, SurfaceTargetRef)
                        and prev_node.kind == TargetKind.SECTION
                        and prev_node.label
                    ):
                        prev_sec = prev_node.label
                        if prev_node.chapter:
                            prev_ch = prev_node.chapter
                        break
                    if isinstance(prev_node, SurfaceScopeBlock):
                        for inner in reversed(prev_node.targets):
                            if not isinstance(inner, SurfaceTargetRef):
                                continue
                            if inner.kind == TargetKind.SECTION and inner.label:
                                prev_sec = inner.label
                                prev_ch = inner.chapter or (
                                    prev_node.scope_label if prev_node.scope_kind == ScopeKind.CHAPTER else prev_ch
                                )
                                break
                        if prev_sec:
                            break
                    if (
                        isinstance(prev_node, SurfaceDescendantCoordination)
                        and prev_node.base.kind == TargetKind.SECTION
                        and prev_node.base.label
                    ):
                        prev_sec = prev_node.base.label
                        if prev_node.base.chapter:
                            prev_ch = prev_node.base.chapter
                        break
                if prev_sec:
                    g_saved = s.save()
                    s.pos += 1  # consume "pykälään"
                    if (t := s.peek()) and t.cat == "COMMA":
                        s.pos += 1
                    # Skip optional provenance/reinstatement qualifiers:
                    # "pykälään, siitä ... kumotun 4 momentin tilalle, uusi N"
                    # "pykälään, sellaisena kuin se on ..., uusi N"
                    s.skip_cats(_REINST_OR_CITE | frozenset({"PROVENANCE_SPAN"}))
                    if (t := s.peek()) and t.cat == "PROV":
                        s.pos = _skip_prov_span(s.tokens, s.pos, len(s.tokens))
                        s.skip_cats(frozenset({"PROVENANCE_SPAN"}))
                    s.skip_cats(_TILALLE_OR_REINST | frozenset({"PROVENANCE_SPAN"}))
                    saved_rt = s.save()
                    _rn = _number_list(s)
                    if _rn and (t := s.peek()) and t.cat in ("MOMENTTI", "KOHTA"):
                        s.pos += 1
                        if (t := s.peek()) and t.cat in _TILALLE_OR_REINST:
                            s.pos += 1
                        else:
                            s.restore(saved_rt)
                    elif _rn:
                        s.restore(saved_rt)
                    if (t := s.peek()) and t.cat == "COMMA":
                        s.pos += 1
                    if (t := s.peek()) and t.cat == "UUSI":
                        s.pos += 1
                        g_nodes = _insertion_sub_target(s, verb, prev_sec, prev_ch, "", 0)
                        if g_nodes:
                            _w_a = _make_witness("fi.anaphoric_pykala_ill", saved, s.pos)
                            for gn_idx, gn in enumerate(g_nodes):
                                if isinstance(gn, SurfaceInsertion) and gn.witness is None:
                                    g_nodes[gn_idx] = SurfaceInsertion(
                                        kind=gn.kind,
                                        label=gn.label,
                                        chapter=gn.chapter,
                                        part=gn.part,
                                        sub_target=gn.sub_target,
                                        witness=_w_a,
                                    )
                                elif isinstance(gn, SurfaceTargetRef) and gn.witness is None:
                                    g_nodes[gn_idx] = SurfaceTargetRef(
                                        kind=gn.kind,
                                        label=gn.label,
                                        chapter=gn.chapter,
                                        part=gn.part,
                                        sub_refs=gn.sub_refs,
                                        notes=gn.notes,
                                        renumber_dest=gn.renumber_dest,
                                        renumber_dest_chapter=gn.renumber_dest_chapter,
                                        renumber_dest_part=gn.renumber_dest_part,
                                        witness=_w_a,
                                    )
                            all_nodes.extend(g_nodes)
                            last_batch_nodes = list(g_nodes)
                            chapter = _extract_chapter_from_nodes(g_nodes, chapter, verb)
                            part = _extract_part_from_nodes(g_nodes, part)
                            while _skip_inline_move_clause_tail(s, all_nodes, last_batch_nodes):
                                pass
                            continue
                    s.restore(g_saved)

            # Consume JOLLOIN_MOVE marker.
            # If the stream carries jolloin renumber pair data (populated by
            # parse() when called from api.py), record this position so that
            # parse() can build a SIIRTAA verb group with SurfaceRenumberTail
            # nodes natively after all verb groups are collected.
            if t2 and t2.cat == "JOLLOIN_MOVE":
                jm_pos = s.pos
                s.pos += 1
                if (
                    s.jolloin_renumber_pairs is not None
                    and jm_pos in s.jolloin_renumber_pairs
                    and s.consumed_jolloin_positions is not None
                ):
                    s.consumed_jolloin_positions.append(jm_pos)
                    if s.consumed_jolloin_contexts is not None:
                        context_nodes = cast(list[SurfaceNode], last_batch_nodes if last_batch_nodes else all_nodes)
                        renumber_ctx = _extract_section_context_from_nodes(
                            context_nodes,
                            VerbGroupContext(chapter=chapter),
                            verb,
                        )
                        s.consumed_jolloin_contexts.append(
                            (jm_pos, renumber_ctx.last_section, renumber_ctx.last_section_chapter)
                        )
                continue

            # Consume sentinel span tokens
            if t2 and t2.cat in SENTINEL_CATEGORIES:
                s.pos += 1
                continue

            # Anaphoric "N momenttiin [, prov ,] uusi sub_target"
            # Handles chains like "ja 3 momenttiin, sellaisena kuin ..., uusi 7 kohta"
            # where the section is inherited from a prior target in all_nodes.
            if t2 and t2.cat == "NUM" and verb == SourceVerb.LISATA:
                _look = s.tokens[s.pos + 1] if s.pos + 1 < len(s.tokens) else None
                if _look and _look.cat == "MOMENTTI" and _look.case == "ILL":
                    _prev_sec = ""
                    _prev_ch = chapter
                    for _prev_node in reversed(all_nodes):
                        if (
                            isinstance(_prev_node, SurfaceInsertion)
                            and _prev_node.kind == TargetKind.SECTION
                            and _prev_node.label
                        ):
                            _prev_sec = _prev_node.label
                            if _prev_node.chapter:
                                _prev_ch = _prev_node.chapter
                            break
                        if (
                            isinstance(_prev_node, SurfaceTargetRef)
                            and _prev_node.kind == TargetKind.SECTION
                            and _prev_node.label
                        ):
                            _prev_sec = _prev_node.label
                            if _prev_node.chapter:
                                _prev_ch = _prev_node.chapter
                            break
                    if _prev_sec:
                        _mom_saved = s.save()
                        _mom_num = int(t2.text) if t2.text.isdigit() else 0
                        s.pos += 2  # consume NUM + MOMENTTI:ILL
                        if (t := s.peek()) and t.cat == "COMMA":
                            s.pos += 1
                        s.skip_cats(_REINST_OR_CITE | frozenset({"PROVENANCE_SPAN"}))
                        if (t := s.peek()) and t.cat == "COMMA":
                            s.pos += 1
                        if _uusi(s):
                            _mom_nodes = _insertion_sub_target(
                                s, verb, _prev_sec, _prev_ch, "", _mom_num
                            )
                            if _mom_nodes:
                                _w_mom = _make_witness("fi.anaphoric_momentti_ill", saved, s.pos)
                                for _mn_idx, _mn in enumerate(_mom_nodes):
                                    if isinstance(_mn, SurfaceInsertion) and _mn.witness is None:
                                        _mom_nodes[_mn_idx] = SurfaceInsertion(
                                            kind=_mn.kind,
                                            label=_mn.label,
                                            chapter=_mn.chapter,
                                            part=_mn.part,
                                            sub_target=_mn.sub_target,
                                            witness=_w_mom,
                                        )
                                all_nodes.extend(_mom_nodes)
                                last_batch_nodes = list(_mom_nodes)
                                chapter = _extract_chapter_from_nodes(_mom_nodes, chapter, verb)
                                part = _extract_part_from_nodes(_mom_nodes, part)
                                while _skip_inline_move_clause_tail(s, all_nodes, last_batch_nodes):
                                    pass
                                continue
                        s.restore(_mom_saved)

            # Anaphoric bare 'uusi N momentti/kohta'
            if t2 and t2.cat == "UUSI" and verb == SourceVerb.LISATA:
                prev_sec = ""
                prev_ch = chapter
                for prev_node in reversed(all_nodes):
                    if (
                        isinstance(prev_node, SurfaceInsertion)
                        and prev_node.kind == TargetKind.SECTION
                        and prev_node.label
                    ):
                        prev_sec = prev_node.label
                        if prev_node.chapter:
                            prev_ch = prev_node.chapter
                        break
                    if (
                        isinstance(prev_node, SurfaceTargetRef)
                        and prev_node.kind == TargetKind.SECTION
                        and prev_node.label
                    ):
                        prev_sec = prev_node.label
                        if prev_node.chapter:
                            prev_ch = prev_node.chapter
                        break
                    if isinstance(prev_node, SurfaceScopeBlock):
                        for inner in reversed(prev_node.targets):
                            if not isinstance(inner, SurfaceTargetRef):
                                continue
                            if inner.kind == TargetKind.SECTION and inner.label:
                                prev_sec = inner.label
                                prev_ch = inner.chapter or (
                                    prev_node.scope_label if prev_node.scope_kind == ScopeKind.CHAPTER else prev_ch
                                )
                                break
                        if prev_sec:
                            break
                    if (
                        isinstance(prev_node, SurfaceDescendantCoordination)
                        and prev_node.base.kind == TargetKind.SECTION
                        and prev_node.base.label
                    ):
                        prev_sec = prev_node.base.label
                        if prev_node.base.chapter:
                            prev_ch = prev_node.base.chapter
                        break
                if prev_sec:
                    au_saved = s.save()
                    s.pos += 1  # consume UUSI
                    au_nodes = _insertion_sub_target(s, verb, prev_sec, prev_ch, "", 0)
                    if au_nodes:
                        _w_au = _make_witness("fi.anaphoric_bare_uusi", saved, s.pos)
                        for an_idx, an_node in enumerate(au_nodes):
                            if isinstance(an_node, SurfaceInsertion) and an_node.witness is None:
                                au_nodes[an_idx] = SurfaceInsertion(
                                    kind=an_node.kind,
                                    label=an_node.label,
                                    chapter=an_node.chapter,
                                    part=an_node.part,
                                    sub_target=an_node.sub_target,
                                    witness=_w_au,
                                )
                            elif isinstance(an_node, SurfaceTargetRef) and an_node.witness is None:
                                au_nodes[an_idx] = SurfaceTargetRef(
                                    kind=an_node.kind,
                                    label=an_node.label,
                                    chapter=an_node.chapter,
                                    part=an_node.part,
                                    sub_refs=an_node.sub_refs,
                                    notes=an_node.notes,
                                    renumber_dest=an_node.renumber_dest,
                                    renumber_dest_chapter=an_node.renumber_dest_chapter,
                                    renumber_dest_part=an_node.renumber_dest_part,
                                    witness=_w_au,
                                )
                        all_nodes.extend(au_nodes)
                        last_batch_nodes = list(au_nodes)
                        chapter = _extract_chapter_from_nodes(au_nodes, chapter, verb)
                        part = _extract_part_from_nodes(au_nodes, part)
                        while _skip_inline_move_clause_tail(s, all_nodes, last_batch_nodes):
                            pass
                        continue
                    s.restore(au_saved)

            # Skip TILALLE / REINST_SPAN / CITATION_SPAN residue token
            if t2 and t2.cat in _TILALLE_OR_REINST:
                s.pos += 1
                continue

            # Heading insertion: "N §:n edelle uusi väliotsikko/luvun otsikko"
            if t2 and (t2.cat == "WORD" or t2.cat == "EDELLA" or t2.cat == "NUM"):
                _hk = s.pos
                _found_heading = False
                _allow_num = t2.cat == "NUM"
                while _hk < len(s.tokens) and _hk < s.pos + 8:
                    tk = s.tokens[_hk]
                    if tk.cat == "UUSI":
                        nxt1 = s.tokens[_hk + 1] if _hk + 1 < len(s.tokens) else None
                        nxt2 = s.tokens[_hk + 2] if _hk + 2 < len(s.tokens) else None
                        if nxt1 and nxt1.cat == "OTSIKKO":
                            s.pos = _hk + 2
                            _found_heading = True
                            break
                        if nxt1 and nxt1.cat == "LUKU" and nxt1.case == "GEN" and nxt2 and nxt2.cat == "OTSIKKO":
                            s.pos = _hk + 3
                            _found_heading = True
                            break
                        if nxt1 and nxt1.cat == "NUM":
                            _sfx = 2
                            if nxt2 and nxt2.cat == "LETTER":
                                _sfx = 3
                            _luku_t = s.tokens[_hk + _sfx] if _hk + _sfx < len(s.tokens) else None
                            _otsikko_t = s.tokens[_hk + _sfx + 1] if _hk + _sfx + 1 < len(s.tokens) else None
                            if (
                                _luku_t
                                and _luku_t.cat == "LUKU"
                                and _luku_t.case == "GEN"
                                and _otsikko_t
                                and _otsikko_t.cat == "OTSIKKO"
                            ):
                                s.pos = _hk + _sfx + 2
                                _found_heading = True
                                break
                    if tk.cat == "VERB":
                        break
                    if tk.cat == "NUM" and not _allow_num:
                        break
                    if tk.cat == "PYKALA" and tk.case not in ("GEN",):
                        break
                    if _allow_num and tk.cat == "NUM":
                        _allow_num = False
                    _hk += 1
                if _found_heading:
                    # When pattern started with NUM, emit a heading placement
                    if t2.cat == "NUM":
                        _sec_num = t2.text
                        _sec_sfx = ""
                        _nxt = s.tokens[_pre_t + 1] if _pre_t + 1 < len(s.tokens) else None
                        if _nxt and _nxt.cat == "LETTER":
                            _sec_sfx = _nxt.lemma
                        _heading_node = SurfaceHeadingPlacement(
                            target_section=_sec_num + _sec_sfx,
                            chapter=chapter,
                            part=part,
                            witness=_make_witness("fi.heading_edelle_luvun_otsikko", _pre_t, s.pos),
                        )
                        all_nodes.append(_heading_node)
                        last_batch_nodes = [_heading_node]
                    if (t := s.peek()) and t.cat == "COMMA":
                        s.pos += 1
                    _pre_h = s.pos
                    nodes = _target(s, verb, chapter, part=part)
                    if nodes:
                        all_nodes.extend(nodes)
                        last_batch_nodes = list(nodes)
                        if _has_doc_ill_in_range(s, _pre_h, s.pos):
                            chapter = ""
                        else:
                            chapter = _extract_chapter_from_nodes(nodes, chapter, verb)
                        part = _extract_part_from_nodes(nodes, part)
                        while _skip_inline_move_clause_tail(s, all_nodes, last_batch_nodes):
                            pass
                    continue

            # Skip inline statute name before structural target
            if t2 and t2.cat == "WORD":
                skip_saved = s.save()
                while (t := s.peek()) and t.cat == "WORD":
                    s.pos += 1
                _pre_w = s.pos
                nodes = _target(s, verb, chapter, part=part)
                if nodes:
                    all_nodes.extend(nodes)
                    last_batch_nodes = list(nodes)
                    if _has_doc_ill_in_range(s, _pre_w, s.pos):
                        chapter = ""
                    else:
                        chapter = _extract_chapter_from_nodes(nodes, chapter, verb)
                    part = _extract_part_from_nodes(nodes, part)
                    while _skip_inline_move_clause_tail(s, all_nodes, last_batch_nodes):
                        pass
                    continue
                s.restore(skip_saved)

            s.restore(saved)
            break

        all_nodes.extend(nodes)
        last_batch_nodes = list(nodes)
        if _has_doc_ill_in_range(s, _pre_t, s.pos):
            chapter = ""
        else:
            chapter = _extract_chapter_from_nodes(nodes, chapter, verb)
        part = _extract_part_from_nodes(nodes, part)
        while _skip_inline_move_clause_tail(s, all_nodes, last_batch_nodes):
            pass

    return _normalize_intrabatch_explicit_part_scope(all_nodes, "")


@dataclass
class VerbGroupContext:
    """Cross-verb-group propagation state."""

    chapter: str = ""
    last_section: str = ""
    last_momentti: int = 0
    last_section_chapter: str = ""


def _extract_section_context_from_nodes(
    nodes: list[SurfaceNode], ctx: VerbGroupContext, verb: SourceVerb | None
) -> VerbGroupContext:
    """Update context from surface nodes for cross-verb-group propagation."""
    new = VerbGroupContext(
        chapter=_extract_chapter_from_nodes(nodes, ctx.chapter, verb or SourceVerb.MUUTTAA),
        last_section=ctx.last_section,
        last_momentti=ctx.last_momentti,
        last_section_chapter=ctx.last_section_chapter,
    )
    for node in reversed(nodes):
        if isinstance(node, SurfaceHeadingPlacement):
            if node.chapter:
                new.last_section_chapter = node.chapter
            break
        if isinstance(node, SurfaceScopeBlock) and node.targets:
            # Extract last section from scope block targets (last target first)
            last_t = node.targets[-1]
            if not isinstance(last_t, SurfaceTargetRef):
                continue
            if last_t.kind == TargetKind.SECTION and last_t.label:
                new.last_section = last_t.label
                # Effective chapter = scope block's label (targets may have empty chapter)
                new.last_section_chapter = node.scope_label if node.scope_kind == ScopeKind.CHAPTER else last_t.chapter
                if last_t.sub_refs:
                    for sr in last_t.sub_refs:
                        if sr.momentti:
                            new.last_momentti = sr.momentti
                            break
            break
        if isinstance(node, SurfaceInsertion) and node.kind == TargetKind.SECTION and node.label:
            new.last_section = node.label
            new.last_section_chapter = node.chapter
            if node.sub_target and node.sub_target.momentti:
                new.last_momentti = node.sub_target.momentti
            break
        if isinstance(node, SurfaceDescendantCoordination) and node.base.kind == TargetKind.SECTION and node.base.label:
            new.last_section = node.base.label
            new.last_section_chapter = node.base.chapter
            for sr in node.arms:
                if sr.momentti:
                    new.last_momentti = sr.momentti
                    break
            break
        if isinstance(node, SurfaceTargetRef) and node.kind == TargetKind.SECTION and node.label:
            new.last_section = node.label
            new.last_section_chapter = node.chapter
            if node.sub_refs:
                for sr in node.sub_refs:
                    if sr.momentti:
                        new.last_momentti = sr.momentti
                        break
            break
    return new


def _parse_relabel_from_context(
    s: Stream,
) -> Optional[SurfaceRelabelFromContext]:
    """Parse a context-dependent section relabel pattern.

    Shape: ``joka siirretään [7 luvun] 61 §:ksi``

    The parser consumes the token syntax but does NOT resolve the source
    section — that requires context from preceding verb groups.  The
    resolver handles the lookback.

    Returns SurfaceRelabelFromContext on success, None on failure.
    """
    saved = s.save()
    dest_chapter = _chapter_ctx(s) or ""
    dest_num = _number_with_suffix(s)
    if dest_num is None:
        s.restore(saved)
        return None
    t = s.peek()
    if not (t and t.cat == "PYKALA" and ":ksi" in (t.text or "").lower()):
        s.restore(saved)
        return None
    s.pos += 1

    dest_section = dest_num[0] + dest_num[1]
    _w = _make_witness("fi.direct_section_relabel", saved, s.pos)
    return SurfaceRelabelFromContext(
        destination_label=dest_section,
        destination_chapter=dest_chapter,
        witness=_w,
    )


def _parse_cross_verb_move_tail(
    s: Stream,
) -> Optional[SurfaceCrossVerbMoveTail]:
    """Parse a cross-verb-group move retarget pattern.

    Shape: ``siirretään muutettu 85 b § 9 lukuun``

    Returns SurfaceCrossVerbMoveTail on success, None on failure.
    The resolver handles the actual retargeting of prior verb group nodes.
    """
    saved = s.save()
    if (t := s.peek()) and t.cat == "WORD" and t.lemma == "muutettu":
        s.pos += 1

    section_data = _number_with_suffix(s)
    if section_data is None:
        s.restore(saved)
        return None
    t = s.peek()
    if not (t and t.cat == "PYKALA" and t.case != "ILL"):
        s.restore(saved)
        return None
    s.pos += 1

    if (t := s.peek()) and t.cat == "COMMA":
        s.pos += 1

    dest_chapter_data = _number_with_suffix(s)
    if dest_chapter_data is None:
        s.restore(saved)
        return None
    t = s.peek()
    if not (t and t.cat == "LUKU" and t.case == "ILL"):
        s.restore(saved)
        return None
    s.pos += 1

    target_label = section_data[0] + section_data[1]
    chapter_label = dest_chapter_data[0] + dest_chapter_data[1]
    return SurfaceCrossVerbMoveTail(
        source_section_label=target_label,
        destination_chapter=chapter_label,
        witness=_make_witness("fi.cross_verb_move_retarget", saved, s.pos),
        move_clause_target_unit_kind="chapter",
    )


def _verb_group(
    s: Stream,
    ctx: VerbGroupContext,
    *,
    preceding_nodes: list[SurfaceNode] | None = None,
) -> tuple[list[SurfaceNode], VerbGroupContext]:
    """Parse one verb group: VERB target_list.

    Returns (nodes, updated_context).
    """
    verb = _verb(s)
    if verb is None:
        return [], ctx
    chapter = ctx.chapter

    # Skip sentinel span tokens after verb
    leading_target_start = s.pos
    s.skip_sentinels()
    started_with_citation_span = (
        leading_target_start < len(s.tokens)
        and s.pos > leading_target_start
        and s.tokens[leading_target_start].cat == "CITATION_SPAN"
    )
    s.skip_cats(frozenset({"TEMPORAL"}))

    # Check if next target starts with explicit chapter or is anaphoric
    effective_chapter = ""
    if chapter:
        t = s.peek()
        if t and t.cat == "LUKU" and t.case == "ILL":
            effective_chapter = chapter
        elif (
            t
            and t.cat == "BACKREF"
            and (t.text or "").lower() in ("mainitun", "mainittu", "mainittujen", "mainituin")
        ):
            t1 = s.peek(1)
            if t1 and t1.cat == "LUKU":
                effective_chapter = chapter

    if verb == SourceVerb.SIIRTAA and preceding_nodes:
        cross_move = _parse_cross_verb_move_tail(s)
        if cross_move is not None:
            return [cross_move], ctx

    if verb == SourceVerb.SIIRTAA and ctx.last_section:
        relabel_node = _parse_relabel_from_context(s)
        if relabel_node is not None:
            return [relabel_node], ctx

    move_dest_part = _leading_move_destination_part(s) if verb == SourceVerb.SIIRTAA else ""

    nodes = _target_list(
        s,
        verb or SourceVerb.MUUTTAA,
        effective_chapter,
        started_with_citation_span_hint=started_with_citation_span,
    )

    if move_dest_part:
        for i, node in enumerate(nodes):
            if isinstance(node, SurfaceTargetRef):
                new_rdp = move_dest_part
                new_rd = node.renumber_dest
                if node.kind == TargetKind.PART and not new_rd:
                    new_rd = move_dest_part
                nodes[i] = SurfaceTargetRef(
                    kind=node.kind,
                    label=node.label,
                    chapter=node.chapter,
                    part=node.part,
                    sub_refs=node.sub_refs,
                    notes=node.notes,
                    renumber_dest=new_rd,
                    renumber_dest_chapter=node.renumber_dest_chapter,
                    renumber_dest_part=new_rdp,
                    witness=node.witness,
                )

    # Anaphoric fallback: if no nodes but we have context, try context-dependent patterns
    if not nodes and ctx.last_section:
        saved_anaphoric = s.save()

        # Pattern: MOMENTTI:ILL uusi N kohta/momentti
        while (t := s.peek()) and t.cat == "WORD":
            s.pos += 1
        t = s.peek()
        if t and t.cat == "MOMENTTI" and t.case == "ILL":
            s.pos += 1
            if (t := s.peek()) and t.cat in _TILALLE_OR_REINST:
                s.pos += 1
            if _uusi(s):
                sub_nodes = _insertion_sub_target(
                    s,
                    verb or SourceVerb.MUUTTAA,
                    ctx.last_section,
                    ctx.last_section_chapter,
                    "",
                    ctx.last_momentti or 1,
                )
                if sub_nodes:
                    _w_cv = _make_witness("fi.cross_verb_momentti", saved_anaphoric, s.pos)
                    for j, sn in enumerate(sub_nodes):
                        if isinstance(sn, SurfaceInsertion) and sn.witness is None:
                            sub_nodes[j] = SurfaceInsertion(
                                kind=sn.kind,
                                label=sn.label,
                                chapter=sn.chapter,
                                part=sn.part,
                                sub_target=sn.sub_target,
                                witness=_w_cv,
                            )
                        elif isinstance(sn, SurfaceTargetRef) and sn.witness is None:
                            sub_nodes[j] = SurfaceTargetRef(
                                kind=sn.kind,
                                label=sn.label,
                                chapter=sn.chapter,
                                part=sn.part,
                                sub_refs=sn.sub_refs,
                                notes=sn.notes,
                                renumber_dest=sn.renumber_dest,
                                renumber_dest_chapter=sn.renumber_dest_chapter,
                                renumber_dest_part=sn.renumber_dest_part,
                                witness=_w_cv,
                            )
                    nodes = sub_nodes

        # Pattern: UUSI N kohta/momentti — bare insertion inheriting section
        if not nodes:
            s.restore(saved_anaphoric)
            while (t := s.peek()) and t.cat == "WORD":
                s.pos += 1
            if _uusi(s):
                sub_nodes = _insertion_sub_target(
                    s,
                    verb or SourceVerb.MUUTTAA,
                    ctx.last_section,
                    ctx.last_section_chapter,
                    "",
                    ctx.last_momentti or 1,
                )
                if sub_nodes:
                    _w_cv2 = _make_witness("fi.cross_verb_bare_uusi", saved_anaphoric, s.pos)
                    for j, sn in enumerate(sub_nodes):
                        if isinstance(sn, SurfaceInsertion) and sn.witness is None:
                            sub_nodes[j] = SurfaceInsertion(
                                kind=sn.kind,
                                label=sn.label,
                                chapter=sn.chapter,
                                part=sn.part,
                                sub_target=sn.sub_target,
                                witness=_w_cv2,
                            )
                        elif isinstance(sn, SurfaceTargetRef) and sn.witness is None:
                            sub_nodes[j] = SurfaceTargetRef(
                                kind=sn.kind,
                                label=sn.label,
                                chapter=sn.chapter,
                                part=sn.part,
                                sub_refs=sn.sub_refs,
                                notes=sn.notes,
                                renumber_dest=sn.renumber_dest,
                                renumber_dest_chapter=sn.renumber_dest_chapter,
                                renumber_dest_part=sn.renumber_dest_part,
                                witness=_w_cv2,
                            )
                    nodes = sub_nodes

        if not nodes:
            s.restore(saved_anaphoric)

    new_ctx = _extract_section_context_from_nodes(nodes, ctx, verb or SourceVerb.MUUTTAA)
    return nodes, new_ctx


def parse(
    tokens: list[Token],
    jolloin_renumber_pairs: dict[int, list[tuple[str, str, str]]] | None = None,
) -> SurfaceClauseModel:
    """Parse filtered token stream into a SurfaceClause.

    This is the core grammar entry point. Expects a clean token stream
    (noise already stripped by Layer 2 filters).

    Args:
        tokens: Filtered token list.
        jolloin_renumber_pairs: Optional mapping from JOLLOIN_MOVE token position
            (in the filtered stream) to renumber pairs extracted from that jolloin
            span.  When provided, the parser emits a SIIRTAA verb group with
            SurfaceTargetRef + SurfaceRenumberTail nodes natively for each jolloin
            renumber, rather than requiring post-parse enrichment in api.py.
            Supply via apply_annotations_with_jolloin_pairs() from scan.py.

    Returns:
        SurfaceClause with verb groups containing SurfaceNode nodes.
    """
    consumed_jolloin_positions: list[int] | None = [] if jolloin_renumber_pairs is not None else None
    consumed_jolloin_contexts: list[tuple[int, str, str]] | None = [] if jolloin_renumber_pairs is not None else None
    s = Stream(
        tokens,
        jolloin_renumber_pairs=jolloin_renumber_pairs,
        consumed_jolloin_positions=consumed_jolloin_positions,
        consumed_jolloin_contexts=consumed_jolloin_contexts,
    )

    # The first verb group in same-clause move constructions can be preceded by
    # a destination-chapter lead-in such as
    #   "lakiin uusi 3 a luku, johon samalla siirretään ...".
    # The main parse loop skips leading non-verb tokens, so we probe that prefix
    # on a throwaway stream before advancing the real stream.
    leading_move_destination_chapter = _leading_move_destination_chapter(Stream(tokens))

    # Skip leading non-verb tokens
    while not s.at_end() and ((_tp := s.peek()) is None or _tp.cat != "VERB"):
        s.pos += 1

    if s.at_end():
        # No VERB tokens found.  Check if this is a meta-only clause
        # (commencement, expiry, transition, delegation) and if so, emit
        # an empty META verb group so downstream resolution can identify
        # meta-only clauses without a structural amendment verb.
        source_text = " ".join(t.text for t in tokens if t.text)
        from lawvm.finland.johtolause.meta_parse import extract_meta_surface_clauses as _extract_meta

        if _extract_meta(source_text):
            meta_vg = SurfaceVerbGroupModel(
                verb=VerbKind.META,
                nodes=(),
            )
            return SurfaceClauseModel(
                verb_groups=(meta_vg,),
                source_text=source_text,
                consumed_count=0,
            )
        return SurfaceClauseModel(verb_groups=())

    # Collect verb groups as (verb_code, start_idx, end_idx) triples
    # into a single flat node list.  Cross-verb-group resolution
    # (SurfaceCrossVerbMoveTail, SurfaceRelabelFromContext) is now
    # deferred to the resolver rather than done inline.
    vg_bounds: list[tuple[SourceVerb, int, int]] = []  # (verb_code, start, end)
    all_nodes_flat: list[SurfaceNode] = []

    ctx = VerbGroupContext()
    nodes, ctx = _verb_group(s, ctx, preceding_nodes=[])
    if nodes:
        verb_code = _find_verb_code_before_nodes(s, nodes)
        if leading_move_destination_chapter and verb_code == SourceVerb.SIIRTAA:
            patched_nodes: list[SurfaceNode] = []
            for node in nodes:
                if isinstance(node, SurfaceTargetRef) and node.kind == TargetKind.SECTION:
                    patched_nodes.append(
                        SurfaceTargetRef(
                            kind=node.kind,
                            label=node.label,
                            chapter=node.chapter or leading_move_destination_chapter,
                            part=node.part,
                            sub_refs=node.sub_refs,
                            notes=node.notes,
                            move_clause_target_unit_kind="chapter",
                            is_exception=node.is_exception,
                            renumber_dest=node.renumber_dest,
                            renumber_dest_chapter=leading_move_destination_chapter,
                            renumber_dest_part=node.renumber_dest_part,
                            witness=node.witness,
                        )
                    )
                else:
                    patched_nodes.append(node)
            nodes = patched_nodes
        start = len(all_nodes_flat)
        all_nodes_flat.extend(nodes)
        vg_bounds.append((verb_code or SourceVerb.MUUTTAA, start, len(all_nodes_flat)))

    # Subsequent verb groups
    while not s.at_end():
        saved = s.save()
        _sep(s)

        if s.at_end():
            break
        _cur = s.peek()
        if _cur is not None and _cur.cat != "VERB":
            while not s.at_end() and ((_tp := s.peek()) is None or _tp.cat != "VERB"):
                s.pos += 1
            if s.at_end():
                break

        # Peek verb code before consuming
        _peeked = s.peek()
        verb_code_next: SourceVerb | None = _peeked.verb_code if _peeked is not None and _peeked.cat == "VERB" else None
        nodes2, ctx = _verb_group(s, ctx, preceding_nodes=all_nodes_flat)
        if not nodes2:
            if s.pos > saved:
                continue
            s.restore(saved)
            break
        start = len(all_nodes_flat)
        all_nodes_flat.extend(nodes2)
        vg_bounds.append((verb_code_next or SourceVerb.MUUTTAA, start, len(all_nodes_flat)))

    # Build the SurfaceClause from the flat list using recorded boundaries.
    # Cross-verb-group retargeting has already been applied to all_nodes_flat.
    verb_groups: list[SurfaceVerbGroupModel] = []
    for vc, vg_start, vg_end in vg_bounds:
        verb_groups.append(
            SurfaceVerbGroupModel(
                verb=VerbKind.from_code(vc),
                nodes=tuple(all_nodes_flat[vg_start:vg_end]),
            )
        )

    # Native jolloin renumber group: if jolloin renumber pairs were consumed
    # during parsing, build a SIIRTAA verb group with SurfaceTargetRef +
    # SurfaceRenumberTail node pairs and prepend it.  This replaces the
    # post-parse enrichment in api.py Phase 1b (e-#1/#2 in the Pro audit).
    if consumed_jolloin_positions and jolloin_renumber_pairs is not None:
        jolloin_context_map = {
            pos: (section_label, section_chapter)
            for pos, section_label, section_chapter in (consumed_jolloin_contexts or [])
        }
        renumber_nodes: list[SurfaceNode] = []
        for jm_pos in consumed_jolloin_positions:
            pairs = jolloin_renumber_pairs.get(jm_pos, [])
            context_section, context_chapter = jolloin_context_map.get(jm_pos, ("", ""))
            for src, dst, pair_kind in pairs:
                if pair_kind == "M":
                    if not context_section:
                        continue
                    renumber_nodes.append(
                        SurfaceTargetRef(
                            kind=TargetKind.SECTION,
                            label=context_section,
                            chapter=context_chapter,
                            sub_refs=(SurfaceSubRef(momentti=int(src)),),
                            notes=("renumber_clause",),
                            witness=SurfaceWitness(rule_id="fi.jolloin_renumber"),
                        )
                    )
                else:
                    target_kind = _surface_target_kind_for_pair_kind(pair_kind)
                    renumber_nodes.append(
                        SurfaceTargetRef(
                            kind=target_kind,
                            label=src,
                            notes=("renumber_clause",),
                            witness=SurfaceWitness(rule_id="fi.jolloin_renumber"),
                        )
                    )
                renumber_nodes.append(
                    SurfaceRenumberTail(
                        new_label=dst,
                        witness=SurfaceWitness(rule_id="fi.jolloin_renumber"),
                    )
                )
        if renumber_nodes:
            renumber_vg = SurfaceVerbGroupModel(
                verb=VerbKind.SIIRTAA,
                nodes=tuple(renumber_nodes),
            )
            verb_groups = [renumber_vg] + verb_groups

    source_text = " ".join(t.text for t in tokens if t.text)
    return SurfaceClauseModel(verb_groups=tuple(verb_groups), source_text=source_text, consumed_count=s.pos)


def _find_verb_code_before_nodes(s: Stream, nodes: list[SurfaceNode]) -> SourceVerb:
    """Find the verb code token that precedes the first node's source span.

    Scans forward from the beginning to find the first VERB token.
    """
    for i in range(len(s.tokens)):
        if s.tokens[i].cat == "VERB":
            return s.tokens[i].verb_code or SourceVerb.MUUTTAA
    return SourceVerb.MUUTTAA
