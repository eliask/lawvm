"""subref — the sub-reference recognizer family (rewrite slice 1).

Extracted from ``grammar.sections`` as a byte-identical relocation: the
sub-reference cluster that recognizes what a section reference lists *after* its
``§`` token (momentti / kohta descendant coordination, section-level facets, the
``edellä oleva otsikko`` heading-CHANGE reference, and content-named
``<descriptor> kohta`` sub-provisions).

The recognizers here are pure functions over the mutable ``_Scan`` scanner that
lives in ``grammar.sections``. They depend on that scanner plus the low-level
number / letter list helpers (``_number_list`` / ``_number`` / ``_letter`` /
``_expand_range_single``) which remain in ``grammar.sections`` because the
section-reference recognizers and emitters there also consume them. ``sections``
re-imports the names defined here so external callers
(``from ...grammar import sections as S; S.SubRef / S._sub_ref / ...``) continue
to resolve unchanged.

Behavior is identical to the pre-extraction ``grammar.sections`` — this module
is a pure relocation, no logic change.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional

from lawvm.core.reference_mention import ProvisionRef
from lawvm.core.semantic_types import FacetKind
from lawvm.finland.johtolause.grammar.combinators import Cursor
from lawvm.finland.johtolause.grammar.sections import (
    _DASH,
    _Scan,
    _expand_range_single,
    _letter,
    _number_list,
    _read,
    _sep,
)
from lawvm.finland.johtolause.lexicon import Token

# ---------------------------------------------------------------------------
# Intermediate sub-reference (mirrors surface_parse.SubRef, recognizer-local).
# ---------------------------------------------------------------------------


_INTRO_WORDS = frozenset({"johdanto", "johdannon"})


def _is_intro_token(token: Token | None) -> bool:
    """Return true for intro-facet words in a sub-reference position."""
    if token is None:
        return False
    if token.cat == "JOHD":
        return True
    return token.cat == "WORD" and (token.text or "").lower() in _INTRO_WORDS


def _consume_intro_token(scan: _Scan) -> bool:
    if _is_intro_token(scan.peek()):
        scan.advance()
        return True
    return False


@dataclass(frozen=True, slots=True)
class SubRef:
    """A parsed sub-reference: momentti, item, facet, or named descriptor.

    ``special`` carries a free-text named-sub-provision descriptor for the
    content-named ``kohta`` forms the numeric/letter coordination cannot model
    (``vekseliä koskeva kohta``, ``1 ryhmän f kohta``, ``Hämeen lääniä koskeva
    kohta``). It mirrors the legacy ``SurfaceSubRef.special`` slot. The known
    facet markers still map onto ``special`` at emit time ("otsikko" / "johd").
    """

    momentti: int = 0  # 0 = whole section
    item: str = ""
    subitem: str = ""  # alakohta label (letter-or-number, mirrors ``item``)
    facet: Optional[FacetKind] = None
    special: str = ""  # free-text named-sub-provision descriptor

    def to_provision_ref(self, statute_id: str, section_label: str) -> ProvisionRef:
        """Lift this sub-reference to a core ``ProvisionRef``.

        ``momentti`` (the subsection) maps to ``ProvisionRef.subsection_num``;
        ``item`` (the kohta) maps to ``item_label``; ``subitem`` (the alakohta)
        maps to ``subitem_label``, exactly mirroring the kohta→item encoding. A
        zero ``momentti`` means the sub-reference names no subsection
        (whole-section / section-level), so ``subsection_num`` is ``None`` rather
        than 0. A facet-only or ``special`` named sub-provision carries no numeric
        subsection/item, so those slots stay ``None`` — the section-level
        reference is the precision we can assert without guessing.
        """
        return ProvisionRef(
            statute_id=statute_id,
            provision_path="",
            section_label=section_label,
            subsection_num=self.momentti if self.momentti else None,
            item_label=self.item or None,
            subitem_label=self.subitem or None,
        )


# ---------------------------------------------------------------------------
# Label helper (faithful to surface_parse).
# ---------------------------------------------------------------------------


def _full_label(num: str, suffix: str) -> str:
    """The label rule: keep the suffix only when the number is not a range."""
    expanded = _expand_range_single(num)
    return num + (suffix if len(expanded) == 1 else "")


# ---------------------------------------------------------------------------
# Letter list (faithful to _letter_list).
# ---------------------------------------------------------------------------


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
# Alakohta (sub-item) tail — the deepest level of the descendant chain
# (luku → § → momentti → kohta → ALAKOHTA).
# ---------------------------------------------------------------------------


def _consume_trailing_alakohta(scan: _Scan) -> Optional[list[str]]:
    """Consume a trailing ``<labels> alakohta`` sub-item run after a kohta.

    The alakohta is the level below the kohta (item). Its label set mirrors the
    kohta's exactly: a letter list (``a``, ``a ja b``, letter ranges via
    ``_letter_list``) OR a number list (``1``, ``1—3`` via ``_number_list``),
    terminated by an ``ALAKOHTA`` token. Returns the list of sub-item labels, or
    ``None`` (rewinding) when no alakohta run is present so the caller's parse is
    unchanged for inputs without a trailing alakohta.

    Letters are tried first because alakohta labels are overwhelmingly lettered
    (``a alakohta``); the numeric arm covers the rarer ``N alakohta`` form.
    """
    saved = scan.pos

    letters = _letter_list(scan)
    if letters:
        _read(scan, _DASH)
    if letters and (t := scan.peek()) and t.cat == "ALAKOHTA":
        scan.advance()
        return letters
    scan.goto(saved)

    nums = _number_list(scan)
    if nums:
        _read(scan, _DASH)
    if nums and (t := scan.peek()) and t.cat == "ALAKOHTA":
        scan.advance()
        return [n + sf for n, sf in nums]
    scan.goto(saved)

    return None


# ---------------------------------------------------------------------------
# Sub-reference recognition (faithful to _sub_ref /
# _parse_descendant_coordination, restricted to the section-ref subset).
# ---------------------------------------------------------------------------


def _parse_after_gen_kohta(scan: _Scan) -> Optional[FacetKind]:
    if _consume_intro_token(scan):
        return FacetKind.INTRO
    return None


def _expand_with_alakohta(scan: _Scan, items: list[SubRef]) -> list[SubRef]:
    """Attach a trailing ``<labels> alakohta`` sub-item run to kohta-level refs.

    Mirrors the kohta→item coordination one level deeper: if an ALAKOHTA run
    follows the just-consumed kohta (``1 kohdan a alakohta``,
    ``1 kohdan a ja b alakohta``), expand each kohta-level ``SubRef`` over the
    sub-item labels, setting ``SubRef.subitem``. When no alakohta follows,
    ``items`` is returned UNCHANGED (no consumption, no field set) so every input
    without a trailing alakohta parses exactly as before. Only called from the
    genitive-kohta descent positions where an alakohta can legally follow.
    """
    subitems = _consume_trailing_alakohta(scan)
    if not subitems:
        return items
    return [replace(it, subitem=sub) for it in items for sub in subitems]


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
                if _consume_intro_token(scan):
                    return [SubRef(mom_ctx, let, facet=FacetKind.INTRO) for let in letters]
                items = [SubRef(mom_ctx, let) for let in letters]
                items = _expand_with_alakohta(scan, items)
                t3 = scan.peek()
                if t3 and t3.cat == "OTSIKKO":
                    scan.advance()
                return items
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
                    items = [
                        SubRef(mom, kn + ksf)
                        for mom in mom_vals
                        for kn, ksf in knums
                    ]
                    expanded = _expand_with_alakohta(scan, items)
                    if expanded is not items:
                        # An alakohta was consumed; the intro/heading facet arm
                        # never co-occurs with a deeper alakohta, so emit the
                        # sub-item-bearing refs directly.
                        return expanded
                    facet = _parse_after_gen_kohta(scan)
                    return [replace(it, facet=facet) for it in items]
                return [SubRef(mom, kn + ksf) for mom in mom_vals for kn, ksf in knums]
            scan.goto(saved_kohta)

            saved_lk = scan.pos
            letters = _letter_list(scan)
            if letters and (t := scan.peek()) and t.cat == "KOHTA":
                scan.advance()
                items = [SubRef(mom, let) for mom in mom_vals for let in letters]
                return _expand_with_alakohta(scan, items)
            if letters:
                scan.goto(saved_lk)

            t3 = scan.peek()
            if _consume_intro_token(scan):
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
            items = [SubRef(mom_ctx, n + sf) for n, sf in nums]
            expanded = _expand_with_alakohta(scan, items)
            if expanded is not items:
                return expanded
            t3 = scan.peek()
            if _consume_intro_token(scan):
                return [replace(it, facet=FacetKind.INTRO) for it in items]
            if t3 and t3.cat == "OTSIKKO":
                scan.advance()  # consume but do not set facet
            return items

        return [SubRef(momentti=mom_ctx, item=n + sf) for n, sf in nums]

    # Numbers without a structural noun: not a valid sub-ref.
    scan.goto(saved)
    return None


# Anaphora / scope cue WORDs that introduce a *different* continuation arm
# (``saman lain``, ``mainitun pykälän``, ``näistä``…) — a descriptor run must not
# start with one of these, else this arm would steal an anaphoric continuation
# the verb-group loop owns.
_DESCRIPTOR_STOPWORDS = frozenset(
    {
        "saman",
        "samaa",
        "mainitun",
        "mainitussa",
        "mainituissa",
        "mainittujen",
        "sanotun",
        "sanotussa",
        "näistä",
        "niistä",
        "nojalla",
        "edellä",
        "oleva",
        "olevan",
        "olevien",
        "kumpikin",
    }
)


def _named_descriptor_subref(scan: _Scan) -> Optional[list[SubRef]]:
    """Recognize a content-named ``<descriptor> kohta`` sub-provision.

    Some ``kohta`` sub-provisions are named by their CONTENT rather than a
    number: ``8 §:n 1 ryhmän f kohta``, ``14 §:n vekseliä koskeva kohta``,
    ``1 §:n Hämeen lääniä koskeva kohta``, ``20 §:n merkkiä 662 a koskeva
    kohta``. Neither the numeric/letter coordination nor the bare ``letter
    kohta`` arm recognizes these (an out-of-vocabulary WORD descriptor sits
    before the terminal ``kohta``), so the section recognizer used to stop at the
    descriptor and the verb-group continuation loop dropped every coordinated
    target after it.

    This arm recognizes the descriptor as a single named sub-provision carried in
    ``SubRef.special`` (a best-effort free-text label, mirroring the legacy
    ``SurfaceSubRef.special`` slot), consuming the whole ``<descriptor> kohta``
    run so the loop continues to the next ``, / ja / sekä <target>``. The
    descriptor is a contiguous run of WORD / NUM / LETTER tokens (with internal
    ``ja`` / ``–`` joiners for compound names such as ``Turun ja Porin lääniä``),
    containing at least one WORD, terminated by a ``KOHTA`` token. It is the LAST
    sub-ref arm tried, so numeric/letter/facet sub-refs are never stolen.
    """
    saved = scan.pos
    tokens = scan.cur.tokens
    n = len(tokens)
    i = scan.pos

    saw_word = False
    desc_parts: list[str] = []
    while i < n:
        tk = tokens[i]
        if tk.cat == "WORD":
            w = (tk.text or "").lower()
            if not saw_word and w in _DESCRIPTOR_STOPWORDS:
                # The run opens with an anaphora/scope cue — not a content name.
                scan.goto(saved)
                return None
            saw_word = True
            desc_parts.append(tk.text or "")
            i += 1
            continue
        if tk.cat in ("NUM", "LETTER"):
            desc_parts.append(tk.text or "")
            i += 1
            continue
        if (
            tk.cat in ("CONJ", "DASH")
            and saw_word
            and i + 1 < n
            and tokens[i + 1].cat in ("WORD", "NUM", "LETTER")
        ):
            # Internal joiner inside a compound name (``Turun ja Porin``,
            # ``kansi- ja konemiehistöä``); keep the run going.
            desc_parts.append(tk.text or "")
            i += 1
            continue
        break

    # Require a content WORD and a terminal KOHTA noun (the descriptor names a
    # ``kohta``). Anything else is not this form — back out cleanly.
    if not saw_word or i >= n or tokens[i].cat != "KOHTA":
        scan.goto(saved)
        return None
    desc_parts.append(tokens[i].text or "")
    scan.goto(i + 1)  # consume through the KOHTA token
    descriptor = " ".join(p for p in desc_parts if p)
    return [SubRef(special=descriptor)]


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


def _sub_ref(scan: _Scan, allow_descriptor: bool = False) -> Optional[list[SubRef]]:
    """Parse a sub-reference after a § token (slice-1 subset).

    Recognizes the section-level facets (otsikko / johdantokappale), the
    ``edellä oleva/olevien otsikko`` heading-CHANGE reference, and the
    momentti/kohta descendant coordination. The heading-INSERT form
    ``N §:n edelle uusi väliotsikko`` (allative ``edelle`` + ``uusi``) is NOT
    recognized here — that is a heading placement, not a section ref, and the
    caller backs out of the whole section ref on a bare 'edelle' lookahead.

    ``allow_descriptor`` enables the content-named ``<descriptor> kohta`` arm
    (``vekseliä koskeva kohta``). It is offered only at the immediate post-``§:n``
    position; elsewhere a WORD-led ``kohta`` run is a coordinated separate target,
    not a sub-ref of this section.
    """
    saved = scan.pos

    t = scan.peek()
    if t and t.cat == "OTSIKKO":
        scan.advance()
        return [SubRef(facet=FacetKind.HEADING)]
    if _consume_intro_token(scan):
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

    # Content-named ``<descriptor> kohta`` sub-provision (last resort): a WORD
    # descriptor naming a kohta by content (``vekseliä koskeva kohta``, ``1
    # ryhmän f kohta``). Recognized so the section recognizer consumes the named
    # sub-provision and the verb-group loop continues to the next coordinated
    # target instead of dropping it. Only at the immediate post-``§:n`` position.
    if allow_descriptor:
        named = _named_descriptor_subref(scan)
        if named is not None:
            return named

    return None


# ---------------------------------------------------------------------------
# Mode-parameterized public entry — the body/cross-statute reference lane.
#
# The amendment lexicon classifies the inessive ``momentissa`` as a bare WORD
# (a pinned HARD CONSTRAINT: amendment johtolauses embed ``N §:n M momentissa
# tarkoitettu …`` relative clauses *inside statute names*, so promoting it in
# the shared lexicon would corrupt amendment target extraction). Body / cross-
# statute references, by contrast, NEED ``1 ja 2 momentissa`` / ``104 §:n 2
# momentissa`` to mean MOMENTTI. ``mode="body"`` applies a local token-
# reclassification pass that promotes those body-only inessive forms to
# MOMENTTI *before* running the shared recognizer — without touching the shared
# lexicon defaults, so amendment mode (``mode="amendment"``, the default) is
# provably byte-identical: the reclassification never runs.
# ---------------------------------------------------------------------------

# Body-only inessive MOMENTTI surface forms the amendment lexicon leaves as
# WORD (statute-name-collision safe to leave as WORD there). In a body
# reference these unambiguously name a subsection, so body mode promotes them.
_BODY_MOMENTTI_WORDS = frozenset({"momentissa", "momenteissa"})


def _reclassify_body_tokens(tokens: list[Token]) -> list[Token]:
    """Promote body-only inessive MOMENTTI WORD forms to MOMENTTI tokens.

    Pure: returns a NEW list, leaving the input (and the shared lexicon)
    untouched. Only the closed ``_BODY_MOMENTTI_WORDS`` set is reclassified, so
    every other token is preserved verbatim. Used solely by the body reference
    lane; amendment parsing never calls it.
    """
    out: list[Token] = []
    for tok in tokens:
        # A body citation often ends the clause at the momentti, so the inessive
        # form can carry a glued sentence-final period (``momentissa.``) the
        # amendment lexer does not strip (it is out-of-vocab). Compare against a
        # period-stripped form so the terminal momentti is still recognized.
        word = (tok.text or "").lower().rstrip(".")
        if tok.cat == "WORD" and word in _BODY_MOMENTTI_WORDS:
            out.append(replace(tok, cat="MOMENTTI", case="NOM", lemma="momentti"))
        else:
            out.append(tok)
    return out


def recognize_sub_refs(
    tokens: list[Token],
    start: int,
    *,
    mode: str = "amendment",
) -> tuple[list[SubRef], int]:
    """Recognize the sub-reference run that follows a ``§`` at ``start``.

    Shared recognizer entry parameterized by ``mode``:

      * ``mode="amendment"`` (default) — current behavior, byte-identical. The
        tokens are scanned as-is by ``_sub_ref``; ``momentissa`` stays a WORD.
      * ``mode="body"`` — runs ``_reclassify_body_tokens`` first so the body-
        only inessive MOMENTTI forms (``momentissa`` …) read as MOMENTTI, then
        runs the SAME ``_sub_ref`` recognizer over the promoted stream. The
        recognizer logic itself is unchanged and shared between both modes.

    Returns ``(sub_refs, end_index)`` — the recognized ``SubRef`` list (empty if
    none) and the token index just past what was consumed.
    """
    if mode not in ("amendment", "body"):
        raise ValueError(f"recognize_sub_refs: unknown mode {mode!r}")
    scan_tokens = _reclassify_body_tokens(tokens) if mode == "body" else tokens
    scan = _Scan(Cursor(scan_tokens, start))
    subs = _sub_ref(scan)
    return (subs or [], scan.pos)
