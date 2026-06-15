"""parser — the verb-group driver over the structural-target recognizer families.

A driver that reproduces ``surface_parse.parse`` for the structural-target
subset of the corpus, built on the per-family recognizers (``sections``,
``insertions``, ``containers``, ``headings``, ``moves``). For any clause whose
shape falls outside the wired families (cross-verb-group resolution, anaphora,
meta / text-amend tails, jolloin, …) it raises :class:`OutOfScope` rather than
guessing — the differential gate only requires 0-delta on the in-scope subsets.

Contract (the entry point the harness drives):

    parse(tokens, jolloin_renumber_pairs=None) -> SurfaceClause

It produces a real frozen ``SurfaceClause`` whose canonical form is
byte-identical to the old parser's on every in-scope clause:

  * ``verb_groups``      — one per amendment verb, with the family's nodes
  * ``meta_clauses`` / ``text_amend_clauses`` / ``target_version_bindings`` — ()
  * ``source_text``      — " ".join(t.text for t in tokens if t.text)
  * ``consumed_count``   — the final cursor position, matching the old parser

Layering: this driver owns the verb-group loop, the token-stream control flow
(the lead-in / separator / trailing skips the old ``Stream`` did), the
per-batch family dispatch, the VALIOTSIKKO heading backref and inline move-tail
co-occurrences, and emission; all within-phrase structure lives in the family
recognizers. It does NOT do cross-verb-group resolution (cross-verb move
retargets / relabels-from-context and the like are out of scope and rejected).

Dispatch order at a target position mirrors the old ``_target``:
``insertion → section_ref → containers → pykala_prefix``. ``section_ref`` runs
before the container family so a section scoped in a chapter (``3 luvun 12 §``)
is owned by the section family and never grabbed by the chapter container
recognizer; the container family declines bare-section shapes itself.
"""

from __future__ import annotations

from typing import Literal, Optional

from lawvm.finland.johtolause.grammar.backrefs import (
    emit_backref_nodes,
    recognize_backref,
)
from lawvm.finland.johtolause.grammar.combinators import Cursor, Span
from lawvm.finland.johtolause.grammar.containers import (
    ContainerForm,
    emit_containers_nodes,
    recognize_containers,
)
from lawvm.finland.johtolause.grammar.jolloin import build_jolloin_group
from lawvm.finland.johtolause.grammar.headings import (
    emit_headings_nodes,
    recognize_heading_after_uusi,
    recognize_heading_edelle_luvun_otsikko,
    recognize_including_preceding_heading_target,
    recognize_trailing_heading_placement,
    recognize_valiotsikko_ref,
)
from lawvm.finland.johtolause.grammar.insertions import (
    OutOfScopeInsertion,
    emit_insertion_nodes,
    insertion_rule_id,
    recognize_insertion,
)
from lawvm.finland.johtolause.grammar.moves import (
    recognize_cross_verb_move_tail,
    recognize_inline_move_tail,
    recognize_relabel_from_context,
    retag_moved_targets,
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
from lawvm.finland.johtolause.grammar.tail import (
    emit_exception_nodes,
    emit_postfix_insert_nodes,
    recognize_exception,
    recognize_postfix_insert,
)
from lawvm.finland.johtolause.lexicon import Token
from lawvm.finland.johtolause.surface_model import (
    ScopeKind,
    SurfaceClause,
    SurfaceDescendantCoordination,
    SurfaceHeadingPlacement,
    SurfaceInsertion,
    SurfaceNode,
    SurfaceScopeBlock,
    SurfaceTargetRef,
    SurfaceVerbGroup,
    SurfaceWitness,
    TargetKind,
    VerbKind,
)
from lawvm.finland.source_verb import SourceVerb

# The recognizer family a recognized target batch belongs to. Tracked per batch
# so the continuation / mixed-batch logic can keep the old parser's grouping
# (it threads scope/anaphora differently across families) and decline rather
# than emit a divergent grouping when families mix in ways this driver does not
# reproduce.
FamilyKind = Literal["section", "insertion", "container", "heading", "move"]


class OutOfScope(Exception):
    """Raised when a clause is not a pure structural-target clause.

    The driver handles only the wired structural-target families; it raises this
    (rather than silently mis-parsing) for any other shape so the differential
    gate can catch-and-skip it.
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


def _container_truncates_section_descendant(scan: _Scan) -> bool:
    """True if the container grabbed a prefix of a section/insertion target.

    Called with the cursor positioned right after ``recognize_containers``
    consumed a ``N luvun`` / ``N osan`` prefix. A run of ``NUM`` / ``LETTER`` /
    ``DASH`` followed by a ``PYKALA`` (a ``N luvun M §`` chapter-/part-scoped
    section, no list separator in between) is a section the section / insertion
    family owns — the container recognizer truncated it. The old parser reaches
    it via ``_insertion`` / ``_section_ref`` first; we decline so the clause
    stays out of scope rather than emitting a bare container node.

    Only a ``PYKALA`` descendant counts: when the chapter is instead followed by
    a bare ``KOHTA`` / ``MOMENTTI`` (``1 luvun 2 kohdan 1 momentin``,
    ``1 luvun 8 kohtaan uuden 2 momentin``) the old parser does NOT treat it as a
    scoped section — its ``_insertion`` / ``_section_ref`` decline and it falls
    through to ``_chapter_ref``, emitting the bare chapter and dropping the tail.
    There the container node IS the old output, so the guard must not fire.

    Faithful exception even for a ``PYKALA`` descendant: on a ``N luvun M §:ään
    uusi ,`` arm — an insertion whose ``uusi`` is *immediately* comma-terminated
    — the old ``_insertion`` likewise declines and ``_target`` falls through to
    ``_chapter_ref``, so the guard must not fire. When the descendant's ``uusi``
    is not comma-terminated (``uusi näin kuuluva …`` / ``uusi N momentti``) or no
    ``uusi`` follows at all, the old parser keeps the section / insertion this
    driver cannot reproduce — so the guard fires (decline).
    """
    toks = scan.cur.tokens
    i = scan.pos
    n = len(toks)
    while i < n and toks[i].cat in ("NUM", "LETTER", "DASH"):
        i += 1
    if i >= n or toks[i].cat != "PYKALA":
        return False
    # Locate a following ``uusi`` insertion anchor before the next list separator
    # / verb / end. If found and immediately comma-terminated, the old parser
    # falls through to the chapter container — keep the container (do not fire).
    j = i + 1
    while j < n:
        cat = toks[j].cat
        if cat in ("VERB", "COMMA", "CONJ", "SEKA"):
            break
        if cat == "UUSI":
            nxt = toks[j + 1].cat if j + 1 < n else "END"
            if nxt == "COMMA":
                return False
            break
        j += 1
    return True


# Anaphor words that open a ``näistä/niistä …`` provenance back-ref the old
# container path re-recognizes into additional kept appendix / table arms.
_PROV_ANAPHOR_WORDS = frozenset({"näistä", "niistä"})


def _has_prov_anaphor_continuation(scan: _Scan) -> bool:
    """True iff a ``näistä/niistä …`` provenance back-ref follows the cursor.

    Faithful to the standalone container driver's guard: the old ``_target_list``
    re-recognizes the bare appendix / table arms such a discourse continuation
    introduces (via machinery outside the clean container family). The driver
    declines (fail-loud) rather than end the list and drop those arms.
    """
    toks = scan.cur.tokens
    for i in range(scan.pos, len(toks)):
        t = toks[i]
        if t.cat == "WORD" and (t.text or "").lower() in _PROV_ANAPHOR_WORDS:
            return True
    return False


_SENTINEL_SPAN_CATS = frozenset(
    {"CITATION_SPAN", "PROVENANCE_SPAN", "STATUTE_NAME_SPAN", "REINST_SPAN"}
)

# Words that open a ``näistä/niistä/joista …`` provenance re-mention back-ref.
# The old ``_skip_provenance_anaphor_backref`` recognizes only ``näistä/niistä``;
# ``joista`` never matches it, so a ``joista`` re-mention arm always falls
# through to normal target parsing and leaks a duplicate.
_PROV_REMENTTION_WORDS = frozenset({"näistä", "niistä", "joista"})
_PROV_BACKREF_CONSUMED_WORDS = frozenset({"näistä", "niistä"})
# Structural sub-noun cats the old anaphor-skip walks past inside one re-mention
# arm (mirrors ``_skip_provenance_anaphor_backref``'s run loop).
_PROV_RUN_CATS = frozenset(
    {"NUM", "LETTER", "DASH", "CONJ", "COMMA", "PYKALA", "MOMENTTI", "KOHTA",
     "ALAKOHTA", "JOHD", "OTSIKKO"}
)
_PROV_ARM_STRUCT_CATS = frozenset({"PYKALA", "MOMENTTI", "KOHTA"})
_PROV_CLOSER_CATS = frozenset({"PROV", "CITATION_SPAN"})

# Separator / sentinel cats that may lead a dropped appendix arm (the old outer
# loop swallows them before the ``liite`` target).
_APPENDIX_LEADIN_CATS = frozenset(
    {"COMMA", "CONJ", "SEKA", "DASH", "TEMPORAL"}
) | _SENTINEL_SPAN_CATS


def _prov_rementtion_leaks(scan: _Scan) -> bool:
    """True iff a ``näistä/niistä/joista`` re-mention tail leaks an old node.

    Faithful reproduction of the OLD ``_skip_provenance_anaphor_backref`` (and
    the target loop that re-parses what it leaves) over the dropped tail. The old
    parser drops the re-mention arm ONLY when it is a single ``näistä/niistä``
    arm that is *closed* by a PROV / CITATION_SPAN provenance trigger; otherwise
    it leaks one or more duplicate operative nodes the new section path drops.
    Concretely the old parser emits an extra node when:

      * the anaphor is ``joista`` (the old backref-skip never recognizes it), or
      * a re-mention arm is NOT closed by a PROV / CITATION_SPAN trigger (the
        skip restores and the arm is re-parsed as a fresh target), or
      * the discourse carries a SECOND structural arm after the first (the skip
        consumes only the first; the ``ja N §`` after it is re-parsed).

    The detector walks ONLY the dropped tail (from the cursor to the next VERB),
    so it never fires on the head list the new parser already reproduced — and a
    pure ``näistä N § sellaisina kuin ne ovat … laissa`` single closed arm (which
    the old parser DOES drop, byte-identical to the new) does not trip it.
    """
    toks = scan.cur.tokens
    n = len(toks)
    # Locate the first prov-rementtion anaphor in the tail (before any VERB).
    i = scan.pos
    anchor: Optional[int] = None
    while i < n:
        t = toks[i]
        if t.cat == "VERB":
            return False
        if t.cat == "WORD" and (t.text or "").lower() in _PROV_REMENTTION_WORDS:
            anchor = i
            break
        i += 1
    if anchor is None:
        return False
    first_word = (toks[anchor].text or "").lower()
    # Mirror the old ``_skip_provenance_anaphor_backref``: greedily walk the WHOLE
    # re-mention run (all sub-noun cats in one pass — a comma/ja-joined list of
    # ``N §[:n M momentti]`` arms is ONE anaphor arm, NOT several), recording
    # whether any structural unit was seen, then require the run to be closed by
    # a PROV / CITATION_SPAN trigger.
    i = anchor + 1
    saw_structural = False
    while i < n and toks[i].cat in _PROV_RUN_CATS:
        if toks[i].cat in _PROV_ARM_STRUCT_CATS:
            saw_structural = True
        i += 1
    if not saw_structural:
        # The old run loop (which excludes ``LUKU``) stalled before any
        # structural unit — a CHAPTER-scoped re-mention ``niistä 24 luvun 11 §
        # …`` — so the old skip restores and re-parses the chapter-scoped section
        # as a fresh duplicate target. The leak cue is a ``LUKU`` exactly where
        # the run stalled (the chapter scope the old loop cannot cross), followed
        # by a chapter-scoped ``PYKALA`` arm — NOT a distant ``§`` belonging to a
        # later, unrelated continuation.
        if i < n and toks[i].cat == "LUKU":
            k = i + 1
            saw_pykala = False
            # Walk the chapter-scoped run (LUKU crossings allowed) to its end.
            while k < n and (toks[k].cat in _PROV_RUN_CATS or toks[k].cat == "LUKU"):
                if toks[k].cat == "PYKALA":
                    saw_pykala = True
                k += 1
            # Only a chapter-scoped re-mention CLOSED by a provenance trigger is a
            # leaked attribution; an unclosed chapter-scoped run is the operative
            # target list the old parser keeps (and the new one reproduces) — must
            # not over-decline it.
            if saw_pykala and k < n and toks[k].cat in _PROV_CLOSER_CATS:
                return True
        return False
    # ``joista`` is never recognized by the old backref-skip, so its arm always
    # falls through to normal target parsing and leaks a duplicate.
    if first_word == "joista":
        return True
    closer = i < n and toks[i].cat in _PROV_CLOSER_CATS
    if not closer:
        # The run is not closed by a PROV / CITATION_SPAN trigger (e.g. it ends in
        # a PROVENANCE_SPAN / STATUTE_NAME_SPAN, or runs straight into ``näin
        # kuuluviksi``): the old skip restores and the whole arm is re-parsed as a
        # fresh duplicate target. The new section path drops it → leak.
        return True
    # The first arm IS closed by a provenance trigger, so the old skip consumes
    # it. A SECOND, separately-attributed ``ja <N> § … [CITE]`` arm joined by a
    # CONJ right after the first closer is re-parsed by the old target loop as a
    # fresh duplicate section ref and leaks — BUT only when that second arm
    # carries a ``PYKALA`` (a re-parseable section target). A second arm that is
    # a bare comma-list continuation (no leading CONJ) or a sub-component-only
    # ``ja 10 kohta [CITE]`` run (KOHTA, no ``§``) the old ``_target`` cannot mint
    # into a node, and it is dropped byte-identically — so those do NOT leak and
    # must not be declined (over-decline of 0-delta). The bare-list multi-arm
    # ``[CITE] 6, 8, … § [CITE]`` continuation is likewise absorbed by the old
    # residue-skipping (no CONJ second-arm head), and is intentionally left as a
    # residual structural delta rather than risk over-declining.
    j = i + 1
    if j < n and toks[j].cat == "CONJ":
        j += 1
        saw_pykala = False
        while j < n and toks[j].cat in _PROV_RUN_CATS:
            if toks[j].cat == "PYKALA":
                saw_pykala = True
            j += 1
        if saw_pykala and j < n and toks[j].cat in _PROV_CLOSER_CATS:
            return True
    return False


def _tail_starts_with_appendix_arm(scan: _Scan) -> bool:
    """True iff the dropped tail's FIRST content is a ``[<doc> ] liite[nä …]``
    appendix arm the old ``_target`` keeps as a trailing APPENDIX target.

    The old parser keeps a trailing ``liite`` / ``liitteenä olevan taulukon``
    appendix target after a section / momentti list (``… 8 §:n sekä päätöksen
    liitteenä olevan taulukon``) that the section family ended the list before —
    so the new parser drops it. The cue must be IMMEDIATE (the LIITE is the next
    operative token after the leading separators / an optional ``päätöksen`` /
    ``asetuksen`` genitive WORD), not a LIITE buried later in the tail behind
    other content the old parser also drops — that would over-decline.
    """
    toks = scan.cur.tokens
    n = len(toks)
    i = scan.pos
    # Require a real list separator (``sekä`` / ``ja`` / ``,``) before the arm:
    # a standalone trailing appendix target (``8 §:n sekä päätöksen liitteenä
    # olevan taulukon``) is separator-led, whereas a ``4 §:n liitteen`` appendix
    # SUB-component of the preceding section carries no separator and is
    # reproduced 0-delta by the section family — must not fire there.
    saw_sep = False
    while i < n and toks[i].cat in _APPENDIX_LEADIN_CATS:
        if toks[i].cat in ("COMMA", "CONJ", "SEKA"):
            saw_sep = True
        i += 1
    if not saw_sep:
        return False
    # An optional ``päätöksen`` / ``asetuksen`` / ``lain`` genitive WORD anchor.
    if i < n and toks[i].cat == "WORD":
        i += 1
    return i < n and toks[i].cat == "LIITE"


def _tail_starts_with_heading_arm(scan: _Scan) -> bool:
    """True iff the dropped tail's FIRST content is a separator-led heading-facet
    arm the old ``_target`` keeps but the section family ended before.

    Two shapes, both separator-led and immediate (so a benign deeper ``otsikko``
    the old parser also drops does not over-decline):

      * a CHAPTER-heading arm ``<N> luvun otsikko`` (the chapter container with a
        HEADING facet — ``…, 4 luvun otsikko``), and
      * a section heading-change arm ``<N> §:n edellä oleva (väli)otsikko`` /
        ``<N> §:n otsikko`` (a SECTION ref carrying the HEADING facet).
    """
    toks = scan.cur.tokens
    n = len(toks)
    i = scan.pos
    saw_sep = False
    while i < n and toks[i].cat in _APPENDIX_LEADIN_CATS:
        if toks[i].cat in ("COMMA", "CONJ", "SEKA"):
            saw_sep = True
        i += 1
    if not saw_sep:
        return False
    if i >= n or toks[i].cat != "NUM":
        return False
    # Walk the leading number(/letter/range) run.
    j = i
    while j < n and toks[j].cat in ("NUM", "LETTER", "DASH", "CONJ", "COMMA"):
        j += 1
    if j >= n:
        return False
    # ``<N> luvun otsikko`` — chapter-heading arm. A LUKU directly followed by an
    # OTSIKKO is the whole-chapter heading; a LUKU followed by a further number /
    # PYKALA run is a chapter-SCOPED section (``4 a luvun 5 a §:n edellä …``), so
    # skip the chapter scope prefix and fall through to the section-arm test.
    if toks[j].cat == "LUKU":
        k = j + 1
        if k < n and toks[k].cat == "OTSIKKO":
            return True
        j = k
        while j < n and toks[j].cat in ("NUM", "LETTER", "DASH", "CONJ", "COMMA"):
            j += 1
        if j >= n:
            return False
    # ``<N> §:n [edellä oleva] (väli)otsikko`` — section heading-facet arm.
    if toks[j].cat == "PYKALA":
        k = j + 1
        return any(
            toks[m].cat in ("OTSIKKO", "VALIOTSIKKO")
            for m in range(k, min(k + 5, n))
        )
    return False


def _section_tail_carries_kept_content(scan: _Scan) -> bool:
    """True iff the dropped SECTION/CONTAINER tail holds nodes the old parser
    keeps or leaks — so the driver must decline rather than silently truncate.

    Unions the faithful keep/leak detectors over the dropped tail: a prov
    re-mention the old parser leaks, a trailing appendix arm it keeps, and a
    dropped ``uusi`` insert arm it keeps. (The heading-change arm is handled
    separately by ``_section_continuation_is_kept``.)
    """
    return (
        _prov_rementtion_leaks(scan)
        or _tail_starts_with_appendix_arm(scan)
        or _tail_starts_with_heading_arm(scan)
    )


def _section_continuation_is_kept(scan: _Scan, has_jolloin: bool = False) -> bool:
    """True if a separator-led continuation the old parser keeps follows.

    Called (with the cursor rewound to the loop-iteration ``saved`` position)
    when ``recognize_section_ref`` declined a separator-led continuation inside a
    SECTION verb group. The old parser keeps two such shapes the section
    recognizer cannot reach; ending the list would silently drop nodes the old
    parser emits, so the driver declines instead:

      * a heading-change arm ``N §:n edellä oleva väliotsikko[n] …`` (a section
        ref carrying a HEADING facet), and
      * a ``näistä/niistä … [CITE/PROV span] ja N §`` resumption, where a fresh
        operative section arm follows the provenance span (the ``ja 20 §`` after
        ``näistä 11 §:n 2 momentti [CITE]``).

    Deliberately narrow so it does NOT match the common
    ``näistä/niistä N [ja N] § sellaisina kuin ne ovat … laissa`` amendment-
    history continuation, which is provenance the old section path drops: there
    the ``ja N §`` precedes the span, so no ``span … CONJ NUM PYKALA`` run exists.
    """
    toks = scan.cur.tokens
    n = len(toks)

    # (1) Heading-change arm ``[seps] NUM … § edellä … (väli)otsikko``.
    i = scan.pos
    while i < n and toks[i].cat in ("COMMA", "CONJ", "SEKA", "DASH"):
        i += 1
    if i < n and toks[i].cat == "NUM":
        j = i
        while j < n and toks[j].cat in ("NUM", "LETTER", "DASH", "CONJ", "COMMA"):
            j += 1
        if j < n and toks[j].cat == "PYKALA":
            j += 1
            saw_edella = any(toks[k].cat == "EDELLA" for k in range(j, min(j + 4, n)))
            saw_heading = any(
                toks[k].cat in ("OTSIKKO", "VALIOTSIKKO") for k in range(j, min(j + 6, n))
            )
            if saw_edella and saw_heading:
                return True

    # (2) ``näistä/niistä … [span] ja N §`` resumption: a provenance span
    # followed (before the next VERB) by a fresh ``[CONJ] NUM … PYKALA`` arm the
    # old parser keeps as an operative target. This token shape is *also* worn by
    # a pure ``näistä N § [CITE] ja N § näin kuuluviksi`` amendment-history
    # provenance the old section path drops, so it is gated on a VALIOTSIKKO
    # heading token being present in the clause: a pure section-reference clause
    # (the section subset) never carries one, so the guard cannot regress it,
    # while the heading-bearing clause where the old parser resumes a kept arm
    # does — exactly the clause this must decline rather than truncate. A clause
    # carrying jolloin renumber pairs is likewise out of the pure-section subset,
    # so ``has_jolloin`` opens the same gate (the old parser keeps the resumed arm
    # there too — declining is fail-loud, not a regression of the section subset).
    if not (has_jolloin or any(t.cat == "VALIOTSIKKO" for t in toks)):
        return False
    i = scan.pos
    saw_anaphor = False
    saw_span_after_anaphor = False
    while i < n:
        t = toks[i]
        if t.cat == "VERB":
            break
        if t.cat == "WORD" and (t.text or "").lower() in _PROV_ANAPHOR_WORDS:
            saw_anaphor = True
        elif saw_anaphor and t.cat in _SENTINEL_SPAN_CATS:
            saw_span_after_anaphor = True
        elif saw_span_after_anaphor and t.cat == "NUM":
            k = i
            while k < n and toks[k].cat in ("NUM", "LETTER", "DASH", "CONJ", "COMMA"):
                k += 1
            if k < n and toks[k].cat == "PYKALA":
                return True
        i += 1
    return False


def _try_recognize_target(
    scan: _Scan, batch_start: int, chapter: str, part: str
) -> Optional[tuple[list[SurfaceNode], FamilyKind]]:
    """Try the structural-target families at the cursor, in old-``_target`` order.

    Order: ``insertion → section_ref → containers → pykala_prefix``. Returns
    ``(nodes, family_kind)`` or None when nothing matched. Insertion nodes get
    the batch witness spanning ``(batch_start, cursor)``; the other families
    carry their own recognizer witness.

    ``section_ref`` precedes the container family so a chapter-scoped section
    (``3 luvun 12 §``) is owned by the section family rather than grabbed by the
    chapter container recognizer (which declines bare-section shapes itself).
    ``pykala_prefix`` (literal ``pykälien …``) is disjoint from every container
    form, so its position after the container family is immaterial.

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
        return _stamp_insertion_batch(nodes, batch_start, scan.pos), "insertion"

    scan.goto(start)
    parsed = recognize_section_ref(scan)
    if parsed is not None:
        return emit_section_nodes(parsed, chapter=chapter, part=part), "section"

    scan.goto(start)
    parsed_container = recognize_containers(scan, chapter=chapter, part=part)
    if parsed_container is not None:
        # A forward ``N luvun`` / ``N osan`` (CHAPTER / PART) immediately followed
        # by a bare section descendant (``N [letter] § / momentti / kohta``, no
        # list separator) is a chapter-/part-scoped *section* the section /
        # insertion families own — the container recognizer truncated it. The old
        # parser reaches it via ``_insertion`` / ``_section_ref`` first; when
        # those decline (an out-of-scope shape) it must NOT fall back to a bare
        # container node. Decline so the clause stays out of scope rather than
        # emitting a divergent container grouping. (The reversed chapter form
        # already self-guards against this inside the recognizer; an APPENDIX /
        # NIMIKE form like ``liitteen 34 §:n 1 momentin`` is NOT scoped this way —
        # the old parser keeps the appendix and drops the ``§`` tail — so the
        # guard only applies to the chapter / part forms.)
        scoped = parsed_container.form in (
            ContainerForm.CHAPTER,
            ContainerForm.CHAPTER_REVERSED,
            ContainerForm.PART,
        )
        if scoped and _container_truncates_section_descendant(scan):
            scan.goto(start)
        else:
            return emit_containers_nodes(parsed_container, chapter=chapter, part=part), "container"

    scan.goto(start)
    parsed = recognize_pykala_prefix_section_ref(scan)
    if parsed is not None:
        return emit_section_nodes(parsed, chapter=chapter, part=part), "section"

    scan.goto(start)
    # ``uusi väliotsikko N §:n edelle`` heading placement at a target position
    # (the old ``_target`` consumes ``uusi`` then calls
    # ``_heading_placement_after_uusi``). The recognizer entry is the OTSIKKO, so
    # the driver consumes the leading ``uusi`` first; on no match the cursor is
    # rewound and the ``uusi``-anchor bail below still applies.
    t = scan.peek()
    if t is not None and t.cat == "UUSI":
        after_uusi = scan.pos + 1
        scan.goto(after_uusi)
        parsed_hp = recognize_heading_after_uusi(scan, chapter, part)
        if parsed_hp is not None:
            return emit_headings_nodes(parsed_hp, chapter=chapter, part=part), "heading"
        scan.goto(start)

    # Nothing parsed here. If an ``uusi`` anchors this very target (an
    # out-of-scope insertion shape the recognizer declined), reject loudly
    # rather than let the driver swallow it as benign residue and silently
    # drop the insertion the old parser would have emitted.
    if _uusi_attached_to_current_target(scan):
        raise OutOfScope("out-of-scope insertion shape (uusi anchor present)")
    return None


def _recognize_one_target(
    scan: _Scan, chapter: str = "", part: str = ""
) -> tuple[list[SurfaceNode], FamilyKind]:
    """Recognize a single target (any wired structural family); emit nodes.

    Records the batch start (the old ``_target`` entry, before its own
    sentinel skip — the witness anchor for insertions), skips sentinel-span
    lead-in and an optional DOC:GEN ("lain 6, 7 ja 18 §"), then dispatches the
    families in old-``_target`` order. Returns ``(nodes, family_kind)``. Raises
    :class:`OutOfScope` if no target is found.
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


def _try_valiotsikko(scan: _Scan, sep_saved: int) -> Optional[list[SurfaceNode]]:
    """Recognize a VALIOTSIKKO heading backref; span starts at ``sep_saved``.

    Faithful to the old parser: the witness span begins at the loop-iteration
    position (before the separator the driver already consumed), so the
    swallowed separator is part of the VALIOTSIKKO node's span.
    """
    parsed = recognize_valiotsikko_ref(scan)
    if parsed is None:
        return None
    from dataclasses import replace as _replace

    parsed = _replace(parsed, span=Span(sep_saved, parsed.span.end))
    return emit_headings_nodes(parsed)


def _try_heading_placement(
    scan: _Scan, chapter: str, part: str
) -> Optional[list[SurfaceNode]]:
    """Recognize a heading-PLACEMENT continuation arm (target-first / window).

    The three target-first heading-placement shapes the old ``_target_list``
    folds into a running target list, tried in old-parser order:

      * ``mukaanluettuna <num_list> §:n edellä olevan väliotsikon`` — an explicit
        preceding-heading facet on a section range (emits SECTION/HEADING refs).
      * ``<num_list> §:n edelle [uusi] väliotsikko`` — a target-first heading
        placement (one ``SurfaceHeadingPlacement`` per target section).
      * the NUM-led ``<N> §:n edelle uusi [<M>] luvun otsikko`` window arm.

    Each recognizer inherits the batch ``chapter`` / ``part`` and rewinds itself
    on no match; returns the emitted nodes or None (cursor untouched on None).
    The ``uusi``-first ``väliotsikko N §:n edelle`` placement is NOT here — it is
    dispatched at a target position by the insertion path (the driver consumes
    the leading ``uusi`` first), mirroring the old ``_heading_placement_after_uusi``.
    """
    saved = scan.pos
    # ``luvun otsikko`` window arm BEFORE the bare-väliotsikko target-list arm:
    # both open on ``<num> §:n edelle [uusi]``, but the old parser routes a
    # ``… edelle uusi [<M>] luvun otsikko`` payload through the window
    # (``fi.heading_edelle_luvun_otsikko``), and only a bare ``väliotsikko``
    # payload through the target-list arm (``fi.heading_edelle_otsikko_target_list``).
    # The luvun-otsikko recognizer is strict (requires the ``luvun otsikko``
    # payload), so trying it first never steals a bare-väliotsikko arm.
    for recognize in (
        recognize_including_preceding_heading_target,
        recognize_heading_edelle_luvun_otsikko,
        recognize_trailing_heading_placement,
    ):
        parsed = recognize(scan, chapter, part)
        if parsed is not None:
            return emit_headings_nodes(parsed, chapter=chapter, part=part)
        scan.goto(saved)
    return None


def _consume_inline_move_tails(
    scan: _Scan, nodes: list[SurfaceNode], last_batch: list[SurfaceNode]
) -> None:
    """Consume any inline move tails retagging ``last_batch`` (old-parser order).

    The old ``_target_list`` calls ``_skip_inline_move_clause_tail`` repeatedly
    after each batch; an inline tail (``…, jotka samalla siirretään N lukuun``)
    retags the immediately preceding whole-section batch in ``nodes`` with its
    move destination (no standalone node is emitted, matching the old output).
    """
    while True:
        saved = scan.pos
        move = recognize_inline_move_tail(scan)
        if move is None:
            scan.goto(saved)
            return
        retag_moved_targets(nodes, last_batch, move)


def _consume_jolloin_move(
    scan: _Scan,
    jolloin_renumber_pairs: dict[int, list[tuple[str, str, str]]] | None,
    consumed_jolloin_positions: list[int] | None,
    consumed_jolloin_contexts: dict[int, tuple[str, str]] | None,
    last_batch: list[SurfaceNode],
    all_nodes: list[SurfaceNode],
) -> bool:
    """Consume a ``JOLLOIN_MOVE`` at the cursor; record its position + context.

    Faithful to the old ``_target_list`` JOLLOIN_MOVE site: advance past the
    marker (it contributes to ``consumed_count``), and — when the stream carries
    renumber-pair data keyed at this position — record the position in
    consumption order and capture the anchor ``(section, chapter)`` from the
    just-parsed batch (``last_batch`` else ``all_nodes``), via the section-context
    extractor. Returns True iff a JOLLOIN_MOVE was at the cursor and consumed.
    """
    t = scan.peek()
    if t is None or t.cat != "JOLLOIN_MOVE":
        return False
    jm_pos = scan.pos
    scan.advance()
    if (
        jolloin_renumber_pairs is not None
        and jm_pos in jolloin_renumber_pairs
        and consumed_jolloin_positions is not None
    ):
        consumed_jolloin_positions.append(jm_pos)
        if consumed_jolloin_contexts is not None:
            context_nodes = last_batch if last_batch else all_nodes
            consumed_jolloin_contexts[jm_pos] = _extract_jolloin_section_context(
                context_nodes
            )
    return True


def _try_exception(
    scan: _Scan, chapter: str, part: str
) -> Optional[list[SurfaceNode]]:
    """Recognize a ``lukuun ottamatta (kuitenkaan)? <sec>`` exception arm.

    The excepted section ref inherits the batch chapter/part (the old
    ``_lukuun_ottamatta_exception`` threads them into its section recognition).
    Returns the re-stamped exception nodes, or None (rewinding ``scan``).
    """
    saved = scan.pos
    parsed = recognize_exception(scan, chapter, part)
    if parsed is None:
        scan.goto(saved)
        return None
    return emit_exception_nodes(parsed, chapter, part)


def _try_backref(scan: _Scan) -> Optional[list[SurfaceNode]]:
    """Recognize a ``mainitun/mainittujen pykälän …`` anaphoric back-ref arm.

    A continuation arm only (the old parser reads a leading bare backref as an
    empty verb group, never a target). The recognizer entry is the BACKREF
    token; the preceding separator the driver already consumed is folded into
    the witness span START — exactly as the VALIOTSIKKO heading backref — so the
    span is rewritten to begin at ``sep_saved``. Returns None (rewinding) on no
    match.
    """
    sep_saved = scan.pos
    parsed = recognize_backref(scan)
    if parsed is None:
        scan.goto(sep_saved)
        return None
    from dataclasses import replace as _replace

    parsed = _replace(parsed, span=Span(sep_saved, parsed.span.end))
    return emit_backref_nodes(parsed)


def _try_postfix_insert(scan: _Scan, part: str) -> Optional[list[SurfaceNode]]:
    """Recognize a ``[lakiin] [uusi] <num> § lukuun <chap> …`` postfix arm.

    Faithful to the old route-2 trailing-postfix continuation: an optional
    ``DOC:ILL`` re-anchor (``…, lakiin uusi …``) then an optional ``uusi`` may
    precede the postfix-chapter insert group. The postfix recognizer requires at
    least one full ``<num> § lukuun <chap>`` arm (else None). On no match the
    cursor is fully rewound so the generic continuation handling is unchanged.
    """
    saved = scan.pos
    t = scan.peek()
    if t and t.cat == "DOC" and t.case == "ILL":
        scan.advance()
    t = scan.peek()
    if t and t.cat == "UUSI":
        scan.advance()
    parsed = recognize_postfix_insert(scan, part)
    if parsed is None:
        scan.goto(saved)
        return None
    return emit_postfix_insert_nodes(parsed, part)


def _parse_verb_group(
    scan: _Scan,
    jolloin_renumber_pairs: dict[int, list[tuple[str, str, str]]] | None = None,
    consumed_jolloin_positions: list[int] | None = None,
    consumed_jolloin_contexts: dict[int, tuple[str, str]] | None = None,
) -> tuple[Optional[SourceVerb], list[SurfaceNode]]:
    """Parse one verb group: VERB then a separator-joined structural-target list.

    Returns (verb_code, nodes). Raises :class:`OutOfScope` on any shape inside
    the group this driver does not reproduce.

    When ``jolloin_renumber_pairs`` is supplied, a ``JOLLOIN_MOVE`` token whose
    position keys the map is consumed in-list (recording its position + the
    just-parsed batch's anchor section context) so ``parse()`` can build and
    prepend the synthetic renumber group, mirroring the old parser.
    """
    t = scan.peek()
    if t is None or t.cat != "VERB":
        raise OutOfScope("expected verb at verb-group start")
    verb = t.verb_code
    scan.advance()

    _skip_sentinels(scan)
    _skip_cat(scan, "TEMPORAL")

    # A SIIRTAA (siirretään) group may be a cross-verb move retarget
    # (``[muutettu] N § M lukuun``) or a relabel-from-context
    # (``[N luvun] M §:ksi``). Both bind to a section established in a *preceding*
    # verb group / the discourse context — cross-verb-group resolution this
    # driver does not perform. Decline rather than mis-read the move's source
    # section as an operative target.
    if verb == SourceVerb.SIIRTAA:
        saved_move = scan.pos
        if recognize_cross_verb_move_tail(scan) is not None:
            raise OutOfScope("cross-verb move retarget (cross-verb-group resolution)")
        scan.goto(saved_move)
        if recognize_relabel_from_context(scan) is not None:
            raise OutOfScope("relabel from context (cross-verb-group resolution)")
        scan.goto(saved_move)

    batch, kind = _recognize_one_target(scan)
    nodes = list(batch)
    last_batch: list[SurfaceNode] = list(batch)
    # Intra-group scope carry-forward: a later bare section list inherits the
    # preceding "N luvun" / "N osan" scope (the old parser threads this between
    # target batches in one verb group).
    chapter = _extract_chapter(batch, "", verb)
    part = _extract_part(batch, "")

    _consume_inline_move_tails(scan, nodes, last_batch)

    while True:
        saved = scan.pos
        nxt = scan.peek()
        if nxt is None:
            break
        if nxt.cat == "VERB":
            break
        if _sep(scan) is None:
            # No conjunction/comma separator. The old ``_target_list``, when
            # ``_sep`` returns None but a sentinel span was absorbed (the cursor
            # advanced), still tries another target at the new position — so two
            # CONTAINER targets separated only by a provenance / citation span
            # (``liitteenä [CITE] liitteen …``) both become nodes. Reproduce that
            # for the container family: if sentinels advanced us and another
            # container target follows, fold it in and continue.
            if kind == "container" and scan.pos != saved:
                try:
                    more, more_kind = _recognize_one_target(scan, chapter, part)
                except OutOfScope:
                    scan.goto(saved)
                else:
                    if more_kind == "container":
                        nodes.extend(more)
                        last_batch = list(more)
                        chapter = _extract_chapter(more, chapter, verb)
                        part = _extract_part(more, part)
                        continue
                    scan.goto(saved)
            # The old parser otherwise ends the target list here and lets the
            # outer loop swallow the tail. For an INSERTION batch a structural
            # tail the old parser keeps folding into the same list (chained
            # ``sekä uusi …`` arms) means ending here drops nodes — decline. For
            # a CONTAINER batch a ``näistä/niistä`` provenance back-ref
            # re-introduces trailing appendix / table arms the old parser keeps —
            # decline. A SECTION batch legitimately reads a trailing out-of-family
            # arm as residue the old section path also drops — do not fail-loud.
            scan.goto(saved)
            if kind == "insertion" and not _tail_is_benign(scan):
                raise OutOfScope("undecodable insertion tail (no separator)")
            if kind == "container" and _has_prov_anaphor_continuation(scan):
                raise OutOfScope("container näistä/niistä provenance continuation")
            if kind in ("section", "container") and _section_tail_carries_kept_content(scan):
                raise OutOfScope("dropped section/container tail keeps old nodes")
            break
        after_sep = scan.peek()
        if after_sep is None or after_sep.cat == "VERB":
            # Trailing separator before a new verb group / end: the outer loop
            # owns this separator, so rewind and let the group end.
            scan.goto(saved)
            break
        # A VALIOTSIKKO heading backref after a separator (the span includes the
        # separator, matching the old parser). It co-occurs inside a section
        # list and is not itself an insertion/section/container batch.
        val_nodes = _try_valiotsikko(scan, saved)
        if val_nodes is not None:
            nodes.extend(val_nodes)
            last_batch = list(val_nodes)
            continue
        # A target-first heading-PLACEMENT arm folded into the running list
        # (``<num_list> §:n edelle uusi väliotsikko`` / the ``luvun otsikko``
        # window / ``mukaanluettuna … edellä olevan väliotsikon``). The old
        # ``_target_list`` recognizes these as continuation targets; the scope
        # is the batch chapter/part already threaded.
        hp_nodes = _try_heading_placement(scan, chapter, part)
        if hp_nodes is not None:
            nodes.extend(hp_nodes)
            last_batch = list(hp_nodes)
            continue
        # A ``, jolloin …`` consequence-renumber sentinel after the separator:
        # record it (context from the just-parsed batch) and continue. The
        # synthetic renumber group is built + prepended after all verb groups.
        if after_sep.cat == "JOLLOIN_MOVE":
            _consume_jolloin_move(
                scan,
                jolloin_renumber_pairs,
                consumed_jolloin_positions,
                consumed_jolloin_contexts,
                last_batch,
                nodes,
            )
            continue
        # A ``lukuun ottamatta (kuitenkaan)? <sec>`` exception carve-out: the
        # excepted section ref is re-stamped is_exception, inheriting the batch
        # chapter/part (faithful to the old ``_lukuun_ottamatta_exception``).
        exc_nodes = _try_exception(scan, chapter, part)
        if exc_nodes is not None:
            nodes.extend(exc_nodes)
            last_batch = list(exc_nodes)
            chapter = _extract_chapter(exc_nodes, chapter, verb)
            part = _extract_part(exc_nodes, part)
            _consume_inline_move_tails(scan, nodes, last_batch)
            continue
        # A ``mainitun/mainittujen pykälän …`` anaphoric back-ref arm: the span
        # folds in the consumed separator (like VALIOTSIKKO). The old parser
        # appends the SurfaceBackRef and continues without updating scope.
        br_nodes = _try_backref(scan)
        if br_nodes is not None:
            nodes.extend(br_nodes)
            last_batch = list(br_nodes)
            continue
        # A trailing ``[lakiin] [uusi] <num> § lukuun <chap>`` postfix-chapter
        # insert arm folded into a SECTION-insert continuation (the old route-2
        # ``_postfix_chapter_section_inserts`` after an optional DOC:ILL re-anchor
        # and ``uusi``). Only an insertion batch reaches this; the emitted SECTION
        # insertions keep the batch's insertion family (no mixed grouping).
        if kind == "insertion":
            pf_nodes = _try_postfix_insert(scan, part)
            if pf_nodes is not None:
                nodes.extend(pf_nodes)
                last_batch = list(pf_nodes)
                chapter = _extract_chapter(pf_nodes, chapter, verb)
                part = _extract_part(pf_nodes, part)
                continue
        try:
            more, more_kind = _recognize_one_target(scan, chapter, part)
        except OutOfScope:
            # The separator led into a continuation this driver cannot parse.
            # For an INSERTION verb group the old parser keeps folding the
            # residue into the same target list (chained ``sekä uusi …`` /
            # postfix-chapter / heading arms), so silently swallowing it would
            # DROP nodes — decline on a non-benign tail. A CONTAINER verb group's
            # kept trailing arms come via a ``näistä/niistä`` provenance back-ref
            # — decline on that. A SECTION-ref group's residue is genuinely benign
            # trailing trivia the old outer loop also skips (``… tilalle uusi …``,
            # a ``sellaisena kuin … N §`` provenance span) and is dropped, EXCEPT
            # the heading-change arm ``N §:n edellä … väliotsikko`` the old parser
            # keeps but the section recognizer cannot reach — decline on that.
            scan.goto(saved)
            if kind == "insertion" and not _tail_is_benign(scan):
                raise OutOfScope("undecodable insertion continuation")
            if kind == "container" and _has_prov_anaphor_continuation(scan):
                raise OutOfScope("container näistä/niistä provenance continuation")
            if kind == "section" and _section_continuation_is_kept(
                scan, jolloin_renumber_pairs is not None
            ):
                raise OutOfScope("undecodable heading-change continuation")
            if kind in ("section", "container") and _section_tail_carries_kept_content(scan):
                raise OutOfScope("dropped section/container tail keeps old nodes")
            break
        # Mixing insertion and non-insertion batches inside one verb group is an
        # out-of-scope shape: the old parser threads scope/anaphora across them
        # in ways this driver does not reproduce. Reject loudly rather than
        # emit a divergent grouping. (Section / container / heading batches DO
        # coexist in one list in the old parser, so those are allowed.)
        if (kind == "insertion") != (more_kind == "insertion"):
            raise OutOfScope("mixed insertion/non-insertion continuation in verb group")
        nodes.extend(more)
        last_batch = list(more)
        chapter = _extract_chapter(more, chapter, verb)
        part = _extract_part(more, part)

        _consume_inline_move_tails(scan, nodes, last_batch)

    return verb, nodes


def _extract_chapter(
    nodes: list[SurfaceNode], current: str, verb: Optional[SourceVerb] = None
) -> str:
    """Chapter scope carried forward from a batch (all wired families).

    A faithful narrowing of ``surface_parse._extract_chapter_from_nodes`` for the
    node types these families emit: the section-family extractor handles scope
    blocks / coordination / section targets; an insertion's ``chapter`` field
    also propagates to a following bare batch in the same verb group; and a
    container CHAPTER target propagates its *label* as scope onto a following
    bare section list, mirroring the old extractor: a whole-chapter target only
    for replace / renumber verbs (M / S, not K / L), a chapter-heading target
    (facet-only sub-refs) always.
    """
    for node in reversed(nodes):
        if isinstance(node, SurfaceInsertion) and node.chapter:
            return node.chapter
        if isinstance(node, SurfaceTargetRef) and node.kind == TargetKind.CHAPTER and node.label:
            if not node.sub_refs:
                if verb not in (SourceVerb.KUMOTA, SourceVerb.LISATA):
                    return node.label
                return current
            if all(sr.facet and not sr.momentti and not sr.item for sr in node.sub_refs):
                return node.label
            return current
    return extract_chapter(nodes, current)


def _extract_part(nodes: list[SurfaceNode], current: str) -> str:
    """Part scope carried forward from a batch (all wired families)."""
    for node in reversed(nodes):
        if isinstance(node, SurfaceInsertion) and node.part:
            return node.part
    return extract_part(nodes, current)


def _extract_jolloin_section_context(nodes: list[SurfaceNode]) -> tuple[str, str]:
    """The anchor ``(section, chapter)`` for a JOLLOIN_MOVE momentti renumber.

    A faithful narrowing of ``surface_parse._extract_section_context_from_nodes``
    to the node types the wired families emit: walk the batch in reverse and
    return the first SECTION-bearing node's ``(label, chapter)`` — for a plain
    section ref, an insertion section, a scope block (whose effective chapter is
    the block's CHAPTER scope label), and a descendant-coordination base. A
    heading placement breaks the walk and contributes only its chapter (no
    section). The empty context ``("", "")`` (no anchor) drops the momentti
    renumber pair, exactly as the old parser does.
    """
    for node in reversed(nodes):
        if isinstance(node, SurfaceHeadingPlacement):
            return "", node.chapter or ""
        if isinstance(node, SurfaceScopeBlock) and node.targets:
            last_t = node.targets[-1]
            if not isinstance(last_t, SurfaceTargetRef):
                continue
            if last_t.kind == TargetKind.SECTION and last_t.label:
                eff_chapter = (
                    node.scope_label
                    if node.scope_kind == ScopeKind.CHAPTER
                    else last_t.chapter
                )
                return last_t.label, eff_chapter or ""
            break
        if (
            isinstance(node, SurfaceInsertion)
            and node.kind == TargetKind.SECTION
            and node.label
        ):
            return node.label, node.chapter or ""
        if (
            isinstance(node, SurfaceDescendantCoordination)
            and node.base.kind == TargetKind.SECTION
            and node.base.label
        ):
            return node.base.label, node.base.chapter or ""
        if (
            isinstance(node, SurfaceTargetRef)
            and node.kind == TargetKind.SECTION
            and node.label
        ):
            return node.label, node.chapter or ""
    return "", ""


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
    """Parse a filtered token stream as a pure structural-target clause.

    Mirrors ``surface_parse.parse`` for the in-scope subset, including the native
    ``jolloin`` consequence-renumber group: when ``jolloin_renumber_pairs`` is
    supplied, the ``JOLLOIN_MOVE`` positions consumed during parsing build a
    synthetic SIIRTAA verb group prepended at index 0. Raises :class:`OutOfScope`
    for any clause outside the wired families.
    """
    source_text = " ".join(t.text for t in tokens if t.text)

    # jolloin renumber accumulators — only when pairs were supplied (mirrors the
    # old parser's ``[] if … is not None else None`` init). Threaded into every
    # verb group so a ``JOLLOIN_MOVE`` keyed in the map records its position +
    # anchor-section context for the prepended renumber group.
    consumed_jolloin_positions: list[int] | None = (
        [] if jolloin_renumber_pairs is not None else None
    )
    consumed_jolloin_contexts: dict[int, tuple[str, str]] | None = (
        {} if jolloin_renumber_pairs is not None else None
    )

    scan = _Scan(Cursor(tokens))

    # Skip leading non-verb tokens (the old parser does the same).
    while not scan.cur.at_end and ((t := scan.peek()) is None or t.cat != "VERB"):
        scan.advance()

    if scan.cur.at_end:
        raise OutOfScope("no amendment verb (meta-only clause)")

    verb_groups: list[SurfaceVerbGroup] = []

    verb, nodes = _parse_verb_group(
        scan,
        jolloin_renumber_pairs,
        consumed_jolloin_positions,
        consumed_jolloin_contexts,
    )
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
        verb2, nodes2 = _parse_verb_group(
            scan,
            jolloin_renumber_pairs,
            consumed_jolloin_positions,
            consumed_jolloin_contexts,
        )
        if not nodes2:
            scan.goto(saved)
            break
        verb_groups.append(
            SurfaceVerbGroup(
                verb=VerbKind.from_code(verb2 or SourceVerb.MUUTTAA), nodes=tuple(nodes2)
            )
        )

    # Native jolloin renumber group: build the synthetic SIIRTAA verb group from
    # the consumed JOLLOIN_MOVE positions + their captured anchor contexts and
    # PREPEND it at index 0 (before every source-order group), exactly as the old
    # parser does. ``None`` (no node produced, e.g. all-M pairs with no anchor)
    # prepends nothing.
    if jolloin_renumber_pairs is not None and consumed_jolloin_positions:
        jolloin_vg = build_jolloin_group(
            consumed_jolloin_positions,
            jolloin_renumber_pairs,
            consumed_jolloin_contexts,
        )
        if jolloin_vg is not None:
            verb_groups = [jolloin_vg] + verb_groups

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
