"""Definition-entry construction parse — Pilot B of the SourceSyntaxGraph.

The FIRST net-new construction-grammar island after the citation-sentence pilot:
the **definition family**. A definition is a formulaic Finnish construction that
introduces a local term and ties it to a definiens:

  * chapeau + enumerated list — ``Tässä laissa tarkoitetaan: 1) X:llä Y…; 2) …``;
    a ``definition_list`` (a colon chapeau governing ``N) <definiendum-adessive>
    <definiens>;`` items);
  * single-sentence — ``X:llä tarkoitetaan tässä laissa Y:tä.`` / ``X tarkoittaa
    Y.``.

Position in the stack
======================
Same discipline as the Pilot-A citation-sentence parse, one family over: a
sentence/block-frame construction with TOTAL TOKEN OWNERSHIP (every char is a
typed construction span, the binding cue, or an EXPLICIT residual; the invariant
is "no silent drop", NOT "no residue"). It is purely ADDITIVE and surface-only —
it makes NO attachment/composition decisions, authorizes NO replay, and is NOT
wired into the production extractor. The CENSUS compares its projection against
the PRODUCTION definition oracle
(``references.defined_terms.recognize_defined_term_bindings``); it does NOT
reimplement the production binding logic — it deliberately MIRRORS the production
enumerated-block item segmentation so where the grammar matches the oracle, the
projection is in parity by construction, and genuine divergences surface as
census miss / superset.

The construction
================
A definition parse over a block/sentence span carries:

  * zero or more **definition entries** — each a ``term_span`` (the definiendum,
    in the adessive/translative as written), a ``definiens_span`` (the right-hand
    side), the **binding cue** (closed list: ``tarkoitetaan`` / ``tarkoittaa``),
    and the **entry marker role** inherited from the ``definition_list`` segment
    (the ``N)`` / ``a)`` list label is NOT in the decoded ``<p>`` coordinate
    space, so the marker is recorded as a role tag, never fabricated);
  * an explicit **residual** span list — every char NOT owned by an entry or the
    chapeau cue, typed by reason. The no-silent-drop invariant is satisfied
    because the residual is EXPLICIT.

A statute-wide ``tarkoitetaan`` definition entry's definiens may itself contain a
cross-statute act cite (``sivutuoteasetuksella tarkoitetaan asetusta (EY) N:o
1069/2009``); the act-cite recognition REUSES the shared act-id recognizers from
the production binder (``_act_id_in_expansion``) — it does NOT reimplement
reference parsing.

:func:`assert_total_ownership` is the checkable postcondition (the union of the
entry spans, the chapeau-cue span, and the residual spans partitions the block
char range exactly).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from lawvm.finland.legal_surface.definitions.shared_definition_parser import (
    enumerated_entry_from_item,
    inline_entry_from_match,
)
from lawvm.finland.references.defined_terms import (
    _ENUM_HEADER,
    _ENUM_ITEM,
    _PRONOUN_ADESSIVE_FORMS,
    _SCOPE_CUE_UNITS,
    _TARKOITETAAN,
    _scope_cue_before,
)

# ---------------------------------------------------------------------------
# Parser-lane provenance — mirrors sentence_parse.parser_lane.
# ---------------------------------------------------------------------------
#: The definition-construction grammar owned the frame (the in-scope, no-silent-
#: drop path).
DEFINITION_LANE_CONSTRUCTION_OWNED = "definition_construction_owned"
#: The frame declined: the span carried a definition cue (``tarkoitetaan`` /
#: ``tarkoittaa``) the family discriminator keyed on, but NO recognizable
#: definiendum entry parsed. Handed back as typed residue, never a guessed parse.
DEFINITION_LANE_DECLINED = "definition_construction_declined"

#: Closed list of definition binding cues (casefolded). Surface-only tags. The
#: enumerated-block + single-sentence definitional idiom anchors on
#: ``tarkoitetaan``; ``tarkoittaa`` is the bare-verb single-sentence variant.
_BINDING_CUES: tuple[str, ...] = ("tarkoitetaan", "tarkoittaa")

#: Role tag recorded when the entry's ``N)`` / ``a)`` enumeration marker is not in
#: the decoded ``<p>`` coordinate space (mirrors the SegmentationGraph's
#: ``definition_entry_marker_not_in_tape`` role).
ENTRY_MARKER_NOT_IN_TAPE = "definition_entry_marker_not_in_tape"


@dataclass(frozen=True)
class Residual:
    """An explicit unowned span of the block (no-silent-drop typed residue)."""

    char_start: int
    char_end: int
    reason: str


@dataclass(frozen=True)
class DefinitionEntry:
    """One defined-term entry the block/sentence carries.

    Attributes:
        term:          The definiendum SURFACE (as written, typically adessive
                       ``-llä``/``-lla``), e.g. ``sivutuotteella``.
        term_start:    Char offset (block-local) where the definiendum begins.
        term_end:      One-past the definiendum.
        definiens:     The definiens (right-hand side) surface text.
        definiens_start: Char offset (block-local) where the definiens begins.
        definiens_end:   One-past the definiens.
        binding_cue:   The closed-list cue (``tarkoitetaan`` / ``tarkoittaa``).
        entry_marker_role: ``ENTRY_MARKER_NOT_IN_TAPE`` for an enumerated entry
                       (the ``N)`` label lives in the dropped ``<num>`` markup),
                       ``""`` for a single-sentence definition.
        scope:         The binding scope inherited from the governing header cue
                       (closed vocabulary: statute/chapter/section/subsection).
        target_ref:    Canonical act id when the definiens is/contains an act cite
                       (shared act-id recognizer), else ``None``.
    """

    term: str
    term_start: int
    term_end: int
    definiens: str
    definiens_start: int
    definiens_end: int
    binding_cue: str
    entry_marker_role: str
    scope: str
    target_ref: str | None


@dataclass(frozen=True)
class DefinitionParse:
    """A definition-block/sentence construction parse (the DefinitionParse-lite IR).

    Attributes:
        seg_start / seg_end: Block char range (block-local coordinates; the parse
                             runs on ``text`` so ``seg_start == 0``).
        text:                The exact block/sentence text.
        kind:                ``"definition_block"`` when >=1 entry parsed from an
                             enumerated header; ``"single_sentence"`` for an inline
                             definition; ``"declined"`` when a definition cue was
                             present but no entry parsed.
        chapeau_cue:         The header cue stem (``tarkoitetaan`` / ``tarkoittaa``)
                             governing the block, or ``""``.
        chapeau_span:        (start, end) of the header cue occurrence, or None.
        entries:             The recognized definition entries, in order.
        residuals:           Explicit unowned spans (the no-silent-drop residue).
        parser_lane:         Which lane produced this frame (closed set above).
    """

    seg_start: int
    seg_end: int
    text: str
    kind: str
    chapeau_cue: str
    chapeau_span: tuple[int, int] | None
    entries: tuple[DefinitionEntry, ...]
    residuals: tuple[Residual, ...] = field(default_factory=tuple)
    parser_lane: str = DEFINITION_LANE_CONSTRUCTION_OWNED


def _has_binding_cue(text_low: str) -> bool:
    return any(cue in text_low for cue in _BINDING_CUES)


def _merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for s, e in sorted(intervals):
        if e <= s:
            continue
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def _fill_residuals(n: int, owned: list[tuple[int, int]], reason: str) -> list[Residual]:
    residuals: list[Residual] = []
    cursor = 0
    for s, e in _merge_intervals(owned):
        if s > cursor:
            residuals.append(Residual(cursor, s, reason))
        cursor = max(cursor, e)
    if cursor < n:
        residuals.append(Residual(cursor, n, reason))
    return residuals


def _parse_enumerated_block(text: str) -> DefinitionParse | None:
    """Parse a header-governed enumerated definitions block, or ``None``.

    MIRRORS the production binder's enumerated-block recognizer
    (``_recognize_enumerated_definitions``) item segmentation: anchored on a
    ``Tässä <unit> tarkoitetaan:`` header, each following ``[:;] <definiendum-run>
    <expansion>;`` item whose leading run is a genuine adessive definiendum
    (``_adessive_phrase_from_run``) becomes an entry inheriting the header scope.
    The entry spans are block-local char offsets. Returns ``None`` when no header
    is present (not this sub-shape).
    """
    h = _ENUM_HEADER.search(text)
    if h is None:
        return None
    scope = _SCOPE_CUE_UNITS[h.group("unit").lower()]
    # Chapeau span: the WHOLE matched header (``Tässä <unit> tarkoitetaan:``) is
    # the construction's owned chapeau — total-ownership checks this same span the
    # residual fill treats as owned. (The bare ``tarkoitetaan`` cue is surfaced
    # separately as ``chapeau_cue`` for reporting.)
    chapeau_span = (h.start(), h.end())

    # Items begin at the header colon (so the first item's ':' delimiter is in
    # scope), exactly like the production binder.
    block_start = h.end() - 1
    entries: list[DefinitionEntry] = []
    # ``owned`` accumulates the EXACT spans :func:`assert_total_ownership` checks
    # (the header cue span + each entry's term span + each entry's definiens span),
    # so the residual fill covers every gap BETWEEN them — the no-silent-drop
    # invariant holds against the same spans the postcondition inspects.
    owned: list[tuple[int, int]] = [(h.start(), h.end())]
    for it in _ENUM_ITEM.finditer(text, block_start):
        run = it.group("run").strip()
        rest = it.group("rest")
        if not run:
            continue
        # UNIFIED with the production binder: the SHARED canonical pipeline
        # (:func:`…shared_definition_parser.enumerated_entry_from_item`) — the SAME
        # function the binder's ``_recognize_enumerated_definitions`` calls — detects
        # the adessive head, TRIMS the left edge (prior-entry connector / adverbial
        # clause) and DECLINES a swept clause fragment. This inherits the precision
        # the forest previously dropped, so the forest no longer over-captures
        # ``sekä vakuutusvuodella`` / ``ja jäteöljyllä``. ``None`` = not a
        # definiendum (no fabrication).
        entry = enumerated_entry_from_item(run, rest.strip(), scope=scope)
        if entry is None:
            continue
        term_surface = entry.term
        # The definiens char boundary is fixed by the (pre-trim) head WORD index the
        # pipeline returned, so the left-trim of the surface never moves the span.
        run_words = run.split()
        head_len = entry.head_word_count
        run_abs_start = it.start("run")
        term_start = run_abs_start
        term_end = run_abs_start + len(run)  # whole run; conservative ownership
        trailing = run_words[head_len:]
        definiens_text = entry.definiens
        rest_start = it.start("rest")
        definiens_start = run_abs_start if trailing else rest_start
        definiens_end = it.end("rest")
        target_ref = entry.target_ref
        entries.append(
            DefinitionEntry(
                term=term_surface,
                term_start=term_start,
                term_end=term_end,
                definiens=definiens_text,
                definiens_start=definiens_start,
                definiens_end=definiens_end,
                binding_cue="tarkoitetaan",
                entry_marker_role=ENTRY_MARKER_NOT_IN_TAPE,
                scope=scope,
                target_ref=target_ref,
            )
        )
        owned.append((term_start, term_end))
        owned.append((definiens_start, definiens_end))

    # MERGE inline ``X:llä tarkoitetaan Y`` entries the enum-item segmentation did
    # not produce — production runs BOTH arms over the same block and merges, so a
    # definiendum the enumerated-item delimiter split missed (but the inline
    # recognizer catches) must be owned too, or it surfaces as a census MISS.
    # Dedup by definition key (the production dedup identity); the enum arm wins
    # for a term it already bound.
    enum_keys = {definition_key(e.term, e.scope, e.target_ref) for e in entries}
    for ie in _inline_entries(text):
        k = definition_key(ie.term, ie.scope, ie.target_ref)
        if k in enum_keys:
            continue
        enum_keys.add(k)
        entries.append(ie)
        owned.append((ie.term_start, ie.term_end))
        owned.append((ie.definiens_start, ie.definiens_end))

    if not entries:
        # A recognized ``Tässä <unit> tarkoitetaan:`` header with NO entries in
        # THIS span is the canonical definitions-block OPENER whose enumerated
        # items live in following segments (the SegmentationGraph splits the
        # chapeau sentence from its items, so the L0 union census feeds the header
        # alone). The header IS a genuine definition construction — own the CHAPEAU
        # span (the M1-derived ``Tässä <unit> tarkoitetaan:`` match), surfacing the
        # rest as explicit residue, rather than declining the whole span and
        # leaving the ``tarkoitetaan`` cue silent-unowned. The projection emits ZERO
        # entry keys (no definiendum was in scope), so the family census is
        # unchanged (empty projection vs empty span-local oracle = match).
        residuals = _fill_residuals(len(text), [chapeau_span], "benign_uninterpreted_prose")
        return DefinitionParse(
            seg_start=0,
            seg_end=len(text),
            text=text,
            kind="definition_header",
            chapeau_cue="tarkoitetaan",
            chapeau_span=chapeau_span,
            entries=(),
            residuals=tuple(residuals),
            parser_lane=DEFINITION_LANE_CONSTRUCTION_OWNED,
        )

    residuals = _fill_residuals(len(text), owned, "benign_uninterpreted_prose")
    return DefinitionParse(
        seg_start=0,
        seg_end=len(text),
        text=text,
        kind="definition_block",
        chapeau_cue="tarkoitetaan",
        chapeau_span=chapeau_span,
        entries=tuple(entries),
        residuals=tuple(residuals),
        parser_lane=DEFINITION_LANE_CONSTRUCTION_OWNED,
    )


def _inline_entries(text: str) -> list[DefinitionEntry]:
    """Recognize INLINE ``X:llä tarkoitetaan Y`` definition entries in ``text``.

    UNIFIED with the production binder's inline ``tarkoitetaan`` recognizer: the
    definiendum, scope, and target are derived by the SHARED canonical pipeline
    (:func:`…shared_definition_parser.inline_entry_from_match`) — the SAME function
    the binder calls — so the forest cannot drift from the production lens. The
    pipeline rejects the referential idiom (``_is_definitional_definiendum`` on the
    head), strips leading scope-locatives / pronoun-adessives, TRIMS the left edge
    (prior-entry connector / adverbial clause) and DECLINES a swept clause fragment
    — the precision the forest previously dropped (which over-captured ``sekä
    vakuutusvuodella`` / ``ja jäteöljyllä``). Scope inherits from the nearest
    preceding definitions-header cue (the offset-bearing look-back stays here).
    Returns the entries in source order (possibly empty). No DefinitionParse
    framing — that is the caller's job.
    """
    entries: list[DefinitionEntry] = []
    for m in _TARKOITETAAN.finditer(text):
        raw_term = m.group("term").strip()
        if not raw_term:
            continue
        expansion_text = m.group("expansion").strip()
        scope = _scope_cue_before(text, m.start("expansion"))
        entry = inline_entry_from_match(text, raw_term, expansion_text, scope)
        if entry is None:
            continue
        # term span: the trailing phrase inside the matched group; approximate to
        # the whole captured-term group (conservative ownership; precise enough).
        entries.append(
            DefinitionEntry(
                term=entry.term,
                term_start=m.start("term"),
                term_end=m.end("term"),
                definiens=entry.definiens,
                definiens_start=m.start("expansion"),
                definiens_end=m.end("expansion"),
                binding_cue="tarkoitetaan",
                entry_marker_role="",
                scope=entry.scope,
                target_ref=entry.target_ref,
            )
        )
    return entries


#: ``tarkoitetaan`` followed by a POST-VERB run of word-tokens (the candidate
#: definiendum phrase + the start of the definiens). Mirrors the enumerated-block
#: header idiom WITHOUT the colon: ``Tässä <unit> tarkoitetaan <X-adessive> Y``.
#: The leading run is handed to ``_adessive_phrase_from_run`` (the SAME production
#: helper the enumerated arm uses), so a post-verb definiens-first shape (a
#: partitive object, ``muuta kuin …``, a cross-reference) yields no adessive head
#: and is correctly declined — no fabrication.
# The definiens run stops at a sentence ``.`` or item ``;`` boundary; ``:`` is
# ADMITTED inside it (an EU act cite ``N:o 1069/2009`` carries a colon). An
# enumerated header's own ``tarkoitetaan:`` never reaches this arm — the
# ``_parse_enumerated_block`` dispatch consumes it first.
_POSTVERB_TARKOITETAAN = re.compile(
    r"tarkoitetaan\b\s+(?P<rest>[^.;]{0,400})",
    re.IGNORECASE,
)

#: Relative / interrogative pronoun surfaces (nominative / oblique) that, anywhere
#: in the pre-verb clause, mark a REFERENTIAL ``…, jo-/mi- … tarkoitetaan …`` ("which
#: is referred to …") — never a post-verb DEFINITION. A post-verb arm only fires
#: when the pre-verb clause carries NO such pronoun (the relative pronoun is the
#: subject of the referential reading, so the post-verb material is the referent
#: list, not a definiendum). Closed set, exact lowercase equality.
_POSTVERB_REFERENTIAL_PRONOUNS: frozenset[str] = frozenset(
    {
        "joka", "jota", "jonka", "jossa", "joita", "joiden", "jotka",
        "joilla", "joille", "joilta", "joina", "joiksi", "joissa", "joista",
        "mikä", "mitä", "minkä", "millä", "miksi",
    }
)


def _postverb_entries(text: str) -> list[DefinitionEntry]:
    """Recognize POST-VERB inline definitions ``tarkoitetaan <X-adessive> Y``.

    The definiendum FOLLOWS ``tarkoitetaan`` here (the enumerated-block idiom
    without the list colon): ``Tässä laissa tarkoitetaan kemikaalilla Y``,
    ``Tässä laissa tarkoitetaan terveydelle vaarallisella kemikaalilla Y``. The
    production binder does NOT cover this (its inline arm requires a PRE-verb
    definiendum and its enumerated arm requires ``tarkoitetaan:``), so owning it is
    a recall gain (a census SUPERSET, never a miss).

    The candidate post-verb run is split into a leading definiendum phrase + the
    definiens by the SAME ``_adessive_phrase_from_run`` head detector the
    enumerated arm uses; the FULL production discipline then applies — the leading
    edge is trimmed (``_trim_to_definiendum_np``) and the phrase is validated as a
    clean definiendum NP (``_is_clean_definiendum_phrase``). A ``None`` head, a
    referential pre-verb pronoun (``joilla … tarkoitetaan``), or a swept clause
    fragment (``… jotka kulkevat omalla``) → NO entry (fail-loud, no guessed
    binding). Scope inherits from the nearest preceding definitions-header cue.
    """
    entries: list[DefinitionEntry] = []
    for m in _POSTVERB_TARKOITETAAN.finditer(text):
        # REFERENTIAL guard: any relative/interrogative pronoun ANYWHERE in the
        # pre-verb clause (back to the last sentence boundary) marks the
        # cross-reference idiom ``…, jo-/mi- … tarkoitetaan …`` ("which is referred
        # to …") — the pronoun is the subject and the post-verb material is the
        # referent, NOT a definiendum. Decline (no guessed binding). The window is
        # the clause preceding the verb (after the last ``.``/``;``/``:``).
        clause_start = max(
            text.rfind(".", 0, m.start()),
            text.rfind(";", 0, m.start()),
            text.rfind(":", 0, m.start()),
        )
        pre = text[clause_start + 1 : m.start()].split()
        if any(w.lower().strip(",") in _POSTVERB_REFERENTIAL_PRONOUNS for w in pre):
            continue
        if pre and pre[-1].lower() in _PRONOUN_ADESSIVE_FORMS:
            continue
        rest = m.group("rest")
        rest_words = rest.split()
        scope = _scope_cue_before(text, m.start())
        # Route the post-verb run through the SHARED canonical pipeline (the SAME
        # head-detect → trim → clean the enumerated arm uses), so the post-verb
        # recall arm cannot drift from the unified definiendum recognition. The
        # definiens boundary is fixed by the (pre-trim) head WORD index the pipeline
        # returns. ``None`` = no adessive head / empty after trim / swept clause
        # fragment → no entry (fail-loud, no guessed binding).
        entry = enumerated_entry_from_item(
            " ".join(rest_words), "", scope=scope
        )
        if entry is None:
            continue
        head_len = entry.head_word_count
        phrase_words = entry.term.split()
        term_surface = entry.term
        rest_start = m.start("rest")
        # Span of the definiendum phrase: from the first trimmed word to the last
        # head word, located precisely against the raw text (whitespace runs).
        head_chars = len(rest) - len(rest.lstrip())
        cursor = rest_start + head_chars
        term_start = cursor
        located_first = False
        for w in rest_words[:head_len]:
            idx = text.index(w, cursor)
            if w == phrase_words[0] and not located_first:
                term_start = idx
                located_first = True
            cursor = idx + len(w)
        term_end = cursor
        definiens_text = " ".join(rest_words[head_len:]).strip()
        definiens_start = term_end
        definiens_end = m.end("rest")
        target_ref = entry.target_ref
        entries.append(
            DefinitionEntry(
                term=term_surface,
                term_start=term_start,
                term_end=term_end,
                definiens=definiens_text,
                definiens_start=definiens_start,
                definiens_end=definiens_end,
                binding_cue="tarkoitetaan",
                entry_marker_role="",
                scope=scope,
                target_ref=target_ref,
            )
        )
    return entries


def _parse_single_sentence(text: str) -> DefinitionParse:
    """Parse a single-sentence inline definition (``X:llä tarkoitetaan Y``).

    Two inline shapes: the canonical PRE-verb definiendum (``X:llä tarkoitetaan
    Y``) and the POST-verb definiendum (``tarkoitetaan <X-adessive> Y``, the
    colon-less header idiom). Declines (no entry, typed residue) when the cue is
    present but neither shape yields a definitional definiendum.
    """
    n = len(text)
    entries = _inline_entries(text)
    if not entries:
        # No PRE-verb definiendum — try the POST-verb shape before declining.
        entries = _postverb_entries(text)
    owned: list[tuple[int, int]] = []
    chapeau_span: tuple[int, int] | None = None
    for e in entries:
        owned.append((e.term_start, e.term_end))
        owned.append((e.definiens_start, e.definiens_end))
        if chapeau_span is None:
            # the binding cue verb (between or before the term, depending on shape)
            verb_idx = text.casefold().find("tarkoitetaan")
            if verb_idx >= 0:
                chapeau_span = (verb_idx, verb_idx + len("tarkoitetaan"))

    if chapeau_span is not None:
        owned.append(chapeau_span)

    if not entries:
        return DefinitionParse(
            seg_start=0,
            seg_end=n,
            text=text,
            kind="declined",
            chapeau_cue="",
            chapeau_span=chapeau_span,
            entries=(),
            residuals=(Residual(0, n, "definition_cue_no_definiendum"),),
            parser_lane=DEFINITION_LANE_DECLINED,
        )

    residuals = _fill_residuals(n, owned, "benign_uninterpreted_prose")
    return DefinitionParse(
        seg_start=0,
        seg_end=n,
        text=text,
        kind="single_sentence",
        chapeau_cue="tarkoitetaan",
        chapeau_span=chapeau_span,
        entries=tuple(entries),
        residuals=tuple(residuals),
        parser_lane=DEFINITION_LANE_CONSTRUCTION_OWNED,
    )


def parse_definition_block(text: str) -> DefinitionParse:
    """Parse one definition block/sentence span into a construction frame.

    ``text`` is the EXACT span (a ``definition_list`` block — the chapeau plus its
    enumerated entries — or a single-sentence inline definition), in its own local
    coordinate system. Single deterministic dispatch: an enumerated
    ``Tässä <unit> tarkoitetaan:`` header → the enumerated-block arm; otherwise the
    single-sentence inline arm. Declines (typed residue, never a guessed parse)
    when a binding cue is present but no definitional definiendum entry parses; the
    caller's family discriminator guarantees the cue is present for in-scope spans.
    """
    if not _has_binding_cue(text.casefold()):
        # Out of family entirely: no binding cue. The whole span is residue.
        return DefinitionParse(
            seg_start=0,
            seg_end=len(text),
            text=text,
            kind="declined",
            chapeau_cue="",
            chapeau_span=None,
            entries=(),
            residuals=(Residual(0, len(text), "not_definition_bearing"),),
            parser_lane=DEFINITION_LANE_DECLINED,
        )
    enum = _parse_enumerated_block(text)
    if enum is not None:
        return enum
    return _parse_single_sentence(text)


def assert_total_ownership(dp: DefinitionParse) -> None:
    """Checkable postcondition: the frame's spans partition ``[seg_start, seg_end)``.

    The union of entry spans (term + definiens), the chapeau-cue span, and the
    explicit residual spans must cover every char of the block with NO gap and NO
    silent drop. Raises ``AssertionError`` on violation.
    """
    n = dp.seg_end - dp.seg_start
    covered = [False] * n
    spans: list[tuple[int, int]] = []
    for e in dp.entries:
        spans.append((e.term_start, e.term_end))
        spans.append((e.definiens_start, e.definiens_end))
    if dp.chapeau_span is not None:
        spans.append(dp.chapeau_span)
    spans.extend((r.char_start, r.char_end) for r in dp.residuals)
    for s, e in spans:
        for i in range(max(0, s), min(n, e)):
            covered[i] = True
    missing = [i for i, c in enumerate(covered) if not c]
    if missing:
        raise AssertionError(
            f"total-ownership violation: {len(missing)} unowned chars in block "
            f"(first gap at {missing[0]}); SILENT DROP. text={dp.text!r}"
        )


# ---------------------------------------------------------------------------
# Projection: DefinitionParse -> [production definition binding key]
# ---------------------------------------------------------------------------


def definition_key(term: str, scope: str, target_ref: str | None) -> str:
    """Canonical census key for one definition entry.

    Keyed on the load-bearing IDENTITY of a definition the production binder emits:
    the definiendum SURFACE (casefolded, whitespace-normalized — the binder's own
    canonical-term key in ``lenses.definitions._canonical_term_id``), its scope,
    and the bound act target (if any). This is the same identity production keys a
    ``DefinedTermBinding`` on, so the projected set is directly comparable to the
    production oracle for the same span.
    """
    norm_term = " ".join(term.strip().lower().split())
    tail = f"|{target_ref}" if target_ref else ""
    return f"{norm_term}|{scope}{tail}"


def projection_definition_keys(dp: DefinitionParse) -> set[str]:
    """The projected definition set as canonical census keys."""
    return {definition_key(e.term, e.scope, e.target_ref) for e in dp.entries}
