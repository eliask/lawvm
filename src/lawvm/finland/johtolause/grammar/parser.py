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

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Optional

from lawvm.finland.johtolause.grammar.backrefs import (
    emit_backref_nodes,
    emit_chapter_backref_nodes,
    emit_part_backref_nodes,
    recognize_backref,
    recognize_chapter_backref,
    recognize_part_backref,
)
from lawvm.finland.johtolause.grammar.combinators import Cursor, Span
from lawvm.finland.johtolause.grammar.containers import (
    ContainerForm,
    emit_containers_nodes,
    recognize_chapter_scoped_subheading,
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
    InsNode,
    OutOfScopeInsertion,
    ParsedAnaphoricSubTarget,
    emit_insertion_nodes,
    insertion_rule_id,
    recognize_bare_anaphoric_chapter_insert,
    recognize_bare_anaphoric_sub_target,
    recognize_cross_verb_anaphoric_insert,
    recognize_insertion,
    recognize_numbered_bare_anaphoric_momentti_insert,
)
from lawvm.finland.johtolause.grammar.moves import (
    apply_leading_move_destination_chapter,
    apply_leading_move_destination_part,
    recognize_cross_verb_move_tail,
    recognize_inline_move_tail,
    recognize_leading_move_destination_chapter,
    recognize_leading_move_destination_part,
    recognize_relabel_from_context,
    retag_moved_targets,
)
from lawvm.finland.johtolause.grammar.sections import (
    _Scan,
    _number_list,
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
    SurfaceRenumberTail,
    SurfaceScopeBlock,
    SurfaceSubRef,
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


@dataclass(frozen=True, slots=True)
class VerbGroupContext:
    """The intra-/cross-verb-group discourse state the driver threads.

    A faithful narrowing of the old ``surface_parse.VerbGroupContext``: the
    anchors a later anaphoric arm (``sanottuun pykälään …`` / a ``jolloin``
    momentti renumber) resolves against. ``last_section`` / ``last_momentti`` are
    the last-mentioned section label and momentti number; ``last_section_chapter``
    is that section's effective chapter; ``chapter`` / ``part`` are the running
    scope carried between target batches.
    """

    last_section: str = ""
    last_section_chapter: str = ""
    last_momentti: int = 0
    chapter: str = ""
    part: str = ""


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
        rule_id = node.witness.rule_id if node.witness is not None else insertion_rule_id(node)
        out.append(
            SurfaceInsertion(
                kind=node.kind,
                label=node.label,
                chapter=node.chapter,
                part=node.part,
                sub_target=node.sub_target,
                witness=SurfaceWitness(rule_id=rule_id, source_span=(start, end)),
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

# Anaphoric determiners that point at the last-mentioned structural unit for an
# INSERT target (``sanottuun lakiin uusi 4 §`` / ``mainittuun pykälään uusi 5
# momentti``). The old parser routes these through
# ``_parse_anaphoric_determiner_insert`` with a distinct
# ``fi.anaphoric_determiner_insert`` witness, so the generic inline-statute-name
# WORD-skip must NOT consume them. Faithful to
# ``surface_parse._ANAPHORIC_INSERT_DETERMINERS``.
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


def _try_skip_provenance_anaphor_backref(scan: _Scan) -> bool:
    """Consume a ``näistä/niistä <ref> sellaisena kuin …`` provenance back-ref.

    Faithful port of ``surface_parse._skip_provenance_anaphor_backref``. The old
    ``_target_list`` calls this after each list separator, BEFORE attempting a
    fresh target: a ``näistä/niistä <ref>`` that is *closed* by a provenance
    trigger (a collapsed CITATION_SPAN or an uncollapsed PROV span) is an
    anaphoric attribution of an already-listed target, not a new one — it is
    skipped, and the loop re-enters to reach any REAL continuation that follows
    the closing citation (``…, näistä 5 §:n 4 momentti [CITE], sekä 7 §`` keeps
    the ``7 §``).

    Returns True (cursor advanced past the closed back-ref) only for the single
    ``näistä/niistä`` arm the old function consumes; otherwise restores the
    cursor and returns False (``joista``, an unclosed run, or a structure-less
    anchor falls through to the existing decline guards, exactly as the old
    parser falls through to ``_target`` and re-parses — which the leak detector
    already classifies). It never silently drops a following target.
    """
    saved = scan.pos
    t = scan.peek()
    if not (t and t.cat == "WORD" and (t.text or "").lower() in _PROV_ANAPHOR_WORDS):
        return False
    scan.advance()

    saw_structural = False
    while (t := scan.peek()) and t.cat in (
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
        scan.advance()

    # Require the run to be closed by a provenance trigger. Without it the anaphor
    # would be a malformed fragment (or a genuine target list); bail out and leave
    # the stream untouched so the existing target / decline path retries.
    if saw_structural and (t := scan.peek()) and t.cat in ("PROV", "CITATION_SPAN"):
        if t.cat == "PROV":
            # An uncollapsed PROV span (the normal pipeline collapses provenance
            # to CITATION_SPAN, so this branch is only reached for raw token
            # streams); reuse the shared span-boundary helper, lazily imported to
            # keep the new package free of a module-level surface_parse edge.
            from lawvm.finland.johtolause.surface_parse import _skip_prov_span

            toks = list(scan.cur.tokens)
            scan.goto(_skip_prov_span(toks, scan.pos, len(toks)))
        else:
            _skip_sentinels(scan)
        return True

    scan.goto(saved)
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
    # it. A SECOND, separately-attributed arm joined by a CONJ or COMMA right
    # after the first closer is re-parsed by the old target loop as a fresh
    # duplicate section ref and leaks — but only when that arm carries a
    # re-parseable ``PYKALA`` and is itself closed by a PROV / CITATION_SPAN
    # trigger. A sub-component-only ``ja 10 kohta [CITE]`` run (KOHTA, no ``§``)
    # the old ``_target`` cannot mint into a node, and a run NOT closed by a
    # trigger is the operative target list the old parser keeps — neither leaks,
    # so neither is declined (over-decline of 0-delta).
    #
    # A CONJ-led second arm is always honoured. The COMMA-led case is honoured
    # ONLY when the ``näistä/niistä`` anchor sits IMMEDIATELY at the dropped-tail
    # cursor (only separators / sentinels between ``scan.pos`` and the anchor).
    # When the old parser TRUNCATED its target list before ever reaching the
    # anchor (an intervening structural arm the new section path also dropped —
    # e.g. a ``96 a §:n johdantolause`` JOHD arm), the old loop never reaches the
    # re-mention and so never leaks; firing there would over-decline a clause the
    # new parser already reproduces 0-delta.
    j = i + 1
    if j < n and toks[j].cat in ("CONJ", "COMMA"):
        comma_led = toks[j].cat == "COMMA"
        if not comma_led or _anchor_is_immediate(toks, scan.pos, anchor):
            j += 1
            saw_pykala = False
            while j < n and toks[j].cat in _PROV_RUN_CATS:
                if toks[j].cat == "PYKALA":
                    saw_pykala = True
                j += 1
            # The second arm leaks when it carries a re-parseable ``§`` (the old
            # target loop mints it into a fresh duplicate node before stopping at
            # its attribution). It is recognized as an attribution arm — rather
            # than the operative target list the old parser keeps — when the ``§``
            # is closed by a PROV / CITATION_SPAN trigger OR by an UNCOLLAPSED
            # ``sellaisena/sellaisina kuin …`` provenance WORD run (the lexer did
            # not fold the appositive into a span). Both forms terminate the old
            # target loop right after the leaked ``§``, so the new section path
            # drops exactly that one node → leak.
            if saw_pykala and j < n and (
                toks[j].cat in _PROV_CLOSER_CATS or _is_provenance_lead_word(toks[j])
            ):
                return True
    return False


# Lead-words of an uncollapsed ``sellaisena / sellaisina kuin …`` provenance
# appositive (including the glued ``sellaisenakuin`` / ``sellaisinakuin``).
_PROV_LEAD_WORDS = frozenset(
    {"sellaisena", "sellaisina", "sellaisenakuin", "sellaisinakuin"}
)


def _is_provenance_lead_word(tok) -> bool:
    """True iff ``tok`` opens an uncollapsed ``sellaisena kuin …`` provenance run."""
    if tok.cat != "WORD":
        return False
    return (tok.text or "").lower() in _PROV_LEAD_WORDS or (
        tok.lemma or ""
    ).lower() in _PROV_LEAD_WORDS


# Separator / sentinel cats that may sit between the dropped-tail cursor and a
# ``näistä/niistä`` re-mention anchor without the old parser having truncated its
# target list first (so the anchor is "immediately" reachable).
_ANCHOR_LEADIN_CATS = frozenset(
    {"COMMA", "CONJ", "SEKA", "DASH"}
) | _SENTINEL_SPAN_CATS


def _anchor_is_immediate(toks, start: int, anchor: int) -> bool:
    """True if only separators / sentinels sit between ``start`` and ``anchor``.

    When any structural token (NUM / PYKALA / MOMENTTI / JOHD / …) intervenes,
    the old parser truncated its target list before reaching the re-mention
    anchor (it never leaks the anchor's arms), so the comma-led leak cue must not
    fire.
    """
    return all(toks[k].cat in _ANCHOR_LEADIN_CATS for k in range(start, anchor))


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
    # An optional document genitive WORD anchor run (``valtioneuvoston
    # päätökseen`` — one or more genitive WORDs), but NOT a provenance opener.
    while i < n and toks[i].cat == "WORD":
        low = (toks[i].text or "").lower()
        if low in _PROV_REMENTTION_WORDS or low.startswith("sella"):
            return False
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
    # An optional anaphoric ``sen`` / ``sitä`` ("its") chapter back-reference WORD
    # before the section heading arm: ``10 luvun ja sen 1 §:n otsikko`` — the old
    # parser keeps the ``1 §:n otsikko`` heading scoped to the resumed chapter.
    if i < n and toks[i].cat == "WORD" and (toks[i].text or "").lower() in ("sen", "sitä"):
        i += 1
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


# Number/scope run cats that a section/insert continuation arm threads before its
# operative noun (``6 §:n 4 kohtaan …``, ``14 ja 15 §:ään …``). Used to walk a
# leading section-address run looking for a kept insertion (``UUSI``) payload.
_INSERT_ARM_RUN_CATS = frozenset(
    {
        "NUM",
        "LETTER",
        "DASH",
        "CONJ",
        "COMMA",
        "SEKA",
        "PYKALA",
        "MOMENTTI",
        "KOHTA",
        "JOHD",
        "LUKU",
        "OSA",
    }
)


def _tail_starts_with_insertion_arm(scan: _Scan) -> bool:
    """True iff the dropped tail's FIRST content is a kept ``uusi …`` insert arm.

    The catastrophic ``insert_list_truncated`` shape: a ``lisätään`` group whose
    first arm parsed as a plain SECTION ref (``6 §:n 4 kohtaan uusi d ja e
    alakohta`` — the section path greedily ate ``6 §:n 4 kohtaan`` and stopped at
    ``uusi``), so the group ran in ``kind="section"`` mode; every following
    ``N §:ään uusi M kohta`` / archaic ``N §:ään uuden M momentin`` insertion arm
    then declines and the loop silently swallows the tail. The old parser keeps
    every one of them.

    The kept tail is a LIST of two or more separator-joined ``uusi …`` insert
    arms (``6 §:ään uusi 6 kohta, 11 §:n 2 momenttiin uusi 6 kohta, …``): the
    old parser folds them all; the section-mode loop, having declined the second
    arm, swallows the whole list. The two-arm requirement is what separates a real
    dropped list from the benign SINGLE trailing ``uusi`` payload of the already-
    captured first arm (``3 §:ään uusi, näin kuuluva 2 momentti`` / ``… uusi``
    run-to-END) that the old parser also drops — those carry no SECOND arm, so the
    guard does not fire and the new parse stays 0-delta.

    Walks ``UUSI``-led / section-address-run-then-``UUSI`` arms separated by list
    separators, counting distinct insert arms up to the next VERB / END. Returns
    True only when at least two are present. A provenance re-mention WORD
    (``sellaisena kuin …`` / ``näistä …``) before the second arm means the tail is
    amendment history the old parser drops — bail.
    """
    toks = scan.cur.tokens
    n = len(toks)
    i = scan.pos
    arms = 0
    while i < n:
        cat = toks[i].cat
        if cat == "VERB":
            break
        if cat in ("COMMA", "CONJ", "SEKA", "DASH", "TEMPORAL"):
            i += 1
            continue
        if cat in _SENTINEL_SPAN_CATS:
            i += 1
            continue
        if cat == "WORD":
            low = (toks[i].text or "").lower()
            if low in _PROV_REMENTTION_WORDS or low.startswith("sella"):
                break
            i += 1
            continue
        # An insert arm: a leading ``UUSI`` or a section-address run reaching
        # ``UUSI`` (``N §:ään … uusi``).
        if cat == "UUSI":
            arms += 1
            i += 1
            continue
        if cat == "NUM":
            j = i
            while j < n and toks[j].cat in _INSERT_ARM_RUN_CATS:
                j += 1
            if j < n and toks[j].cat == "UUSI":
                arms += 1
                i = j + 1
                continue
            # A NUM run not closing on ``UUSI`` — a fresh non-insert target the old
            # parser handles separately; stop scanning the insert list here.
            break
        # Any other token ends the insert-list scan.
        break
    if arms >= 2:
        return True
    # A SINGLE arm is benign (the trailing payload of an already-captured first
    # arm) EXCEPT a whole-section insert into a named document: ``lakiin uusi 15 a
    # ja 27 a §`` — a fresh ``<doc-WORD(illative)> uusi <num> §`` the old parser
    # keeps and that carries no preceding section address, so it cannot be a
    # trailing payload of a prior arm. Detect that one kept single-arm shape.
    return _tail_starts_with_document_whole_section_insert(scan)


# Illative-case document anchor WORDs that introduce a whole-section insert into a
# named document (``lakiin uusi 15 a §`` / ``asetukseen uusi 25 a §``).
_DOC_ILLATIVE_INSERT_WORDS = frozenset(
    {
        "lakiin",
        "asetukseen",
        "päätökseen",
        "työjärjestykseen",
        "johtosääntöön",
        "ohjesääntöön",
    }
)


def _tail_starts_with_document_whole_section_insert(scan: _Scan) -> bool:
    """True iff the dropped tail's FIRST content is a ``<doc-WORD> uusi <num> §``
    whole-section insert into a named document the old parser keeps.

    The cue must be IMMEDIATE: separators, then a document illative WORD, then
    ``UUSI``, then a section-address run closing on ``PYKALA``. A preceding
    section-address run (``N §:ään uusi …``) is NOT this shape — that is handled
    by the multi-arm path (and a single such arm is the benign trailing payload).
    """
    toks = scan.cur.tokens
    n = len(toks)
    i = scan.pos
    # A stranded ``uusi`` payload of the already-captured first arm may lead the
    # tail (``14 §:n 1 momenttiin`` was emitted as a section, leaving its ``uusi``
    # [+ kohta/momentti run] unconsumed) — skip it to reach the separator before
    # the kept ``lakiin uusi N §`` arm.
    if i < n and toks[i].cat == "UUSI":
        i += 1
        while i < n and toks[i].cat in ("NUM", "LETTER", "DASH", "KOHTA", "MOMENTTI"):
            i += 1
    while i < n and toks[i].cat in ("COMMA", "CONJ", "SEKA", "DASH", "TEMPORAL"):
        i += 1
    # The document anchor lexes as a DOC token (``lakiin``/``asetukseen``) or, for
    # less-common documents, a bare illative WORD.
    if i >= n:
        return False
    if toks[i].cat == "DOC":
        i += 1
    elif toks[i].cat == "WORD" and (toks[i].text or "").lower() in _DOC_ILLATIVE_INSERT_WORDS:
        i += 1
    else:
        return False
    if i >= n or toks[i].cat != "UUSI":
        return False
    i += 1
    j = i
    while j < n and toks[j].cat in ("NUM", "LETTER", "DASH", "CONJ", "COMMA", "SEKA"):
        j += 1
    return j < n and toks[j].cat == "PYKALA"


# Anaphoric re-mention determiners that re-introduce a kept operative target in a
# dropped tail (``…, sanottu 10 § edelleen …`` / ``näiden ohella 3 §:n …``). The
# old parser keeps the re-mentioned section as a fresh operative ref; the new
# section path cannot reach past the WORD lead-in, so it would silently drop it.
_REMENTION_LEADIN_WORDS = frozenset({"sanottu", "sanotut", "näiden", "niiden"})

# Separators + sentinel spans skipped before a re-mention determiner in a dropped
# tail (the ``, [CITE] ja sanottu 10 §`` shape interposes a citation span).
_REMENTION_LEADIN_SKIP_CATS = frozenset(
    {"COMMA", "CONJ", "SEKA", "DASH"}
) | _SENTINEL_SPAN_CATS


def _tail_starts_with_rementioned_section_arm(scan: _Scan) -> bool:
    """True iff the dropped tail's FIRST content is a ``<determiner> <sec> …`` arm
    the old parser keeps as an operative section ref.

    Covers the ``mid_list_sanottu`` re-mention (``…, ja sanottu 10 § edelleen
    osittain muutettuna …`` — the old parser re-emits the ``10 §`` as a second
    operative target) and the ``näiden ohella <sec> …`` connective. The lead-in
    determiner WORD(s) lead straight into a structural ``<num> … §`` ref the
    section continuation cannot skip the WORD prefix to reach.
    """
    toks = scan.cur.tokens
    n = len(toks)
    i = scan.pos
    while i < n and toks[i].cat in _REMENTION_LEADIN_SKIP_CATS:
        i += 1
    if i >= n or toks[i].cat != "WORD":
        return False
    if (toks[i].text or "").lower() not in _REMENTION_LEADIN_WORDS:
        return False
    # The determiner must lead (within a short window of WORD/NUM/LETTER/DASH) into
    # a structural section ref — the kept operative target.
    for k in range(i + 1, min(i + 5, n)):
        if toks[k].cat == "PYKALA":
            return True
        if toks[k].cat not in ("NUM", "LETTER", "DASH", "WORD"):
            break
    return False


def _tail_starts_with_part_scoped_arm(scan: _Scan) -> bool:
    """True iff the dropped tail's FIRST content is a part-scope resumption arm
    (``…, II A osan 1 luvun 1 §, …``) the old parser keeps.

    A mid-list explicit PART switch (``<num> [letter] osan …``) re-anchors the
    following chapter/section descendants under the new part; the new section
    continuation stops before the OSA-scoped arm, silently dropping the resumed
    targets. The cue is separator-led ``<num> [letter] osa`` immediately leading
    into a structural ``luku`` / ``§`` descendant before any provenance opener.
    """
    toks = scan.cur.tokens
    n = len(toks)
    i = scan.pos
    saw_sep = False
    while i < n and toks[i].cat in ("COMMA", "CONJ", "SEKA", "DASH"):
        saw_sep = True
        i += 1
    if not saw_sep or i >= n or toks[i].cat != "NUM":
        return False
    j = i + 1
    while j < n and toks[j].cat in ("NUM", "LETTER", "DASH"):
        j += 1
    if j >= n or toks[j].cat != "OSA":
        return False
    # The part anchor must lead into a structural descendant (a chapter or
    # section), not a bare trailing ``osa`` the old parser also drops.
    k = j + 1
    while k < n and toks[k].cat in ("NUM", "LETTER", "DASH"):
        k += 1
    return k < n and toks[k].cat in ("LUKU", "PYKALA")


def _tail_starts_with_second_statute_arm(scan: _Scan) -> bool:
    """True iff the dropped tail's FIRST content is a second named statute's
    target the old parser keeps (``multi_statute_target``).

    A multi-statute repeal/amend (``kumotaan … asetuksen 29 §, oikeudenkäymis-
    kaaren 4 luvun 2 §, niinkuin …``): the first statute's target is reproduced,
    then a fresh ``<statute-name WORD …> N luvun N §`` arm for a SECOND statute
    follows. The new section path stops at the inline statute-name WORD run; the
    old parser skips it and keeps the ``N luvun N §``. The cue is a separator-led
    ``STATUTE_NAME_SPAN`` (or a WORD run) immediately leading into a ``<num>
    luvun <num> §`` chapter-scoped section before any provenance opener.
    """
    toks = scan.cur.tokens
    n = len(toks)
    i = scan.pos
    saw_sep = False
    while i < n and toks[i].cat in ("COMMA", "CONJ", "SEKA", "DASH"):
        saw_sep = True
        i += 1
    if not saw_sep:
        return False
    # A statute-name span or an inline statute-name WORD run (``oikeudenkäymis-
    # kaaren``) introduces the second statute.
    if i < n and toks[i].cat == "STATUTE_NAME_SPAN":
        i += 1
    else:
        seen_word = False
        while i < n and toks[i].cat == "WORD":
            low = (toks[i].text or "").lower()
            if low in _PROV_REMENTTION_WORDS or low.startswith("sella"):
                return False
            seen_word = True
            i += 1
        if not seen_word:
            return False
    # Skip an optional sentinel span lead-in.
    while i < n and toks[i].cat in _SENTINEL_SPAN_CATS:
        i += 1
    # A chapter-scoped ``<num> luvun <num> §`` section of the second statute.
    if i >= n or toks[i].cat != "NUM":
        return False
    j = i + 1
    while j < n and toks[j].cat in ("NUM", "LETTER", "DASH"):
        j += 1
    if j < n and toks[j].cat == "LUKU":
        k = j + 1
        while k < n and toks[k].cat in ("NUM", "LETTER", "DASH"):
            k += 1
        return k < n and toks[k].cat == "PYKALA"
    return False


def _tail_starts_with_minka_ohella_arm(scan: _Scan) -> bool:
    """True iff the dropped tail is a ``minkä ohella <sec> … muutetaan`` arm the
    old parser keeps as an operative section ref.

    Real source family (``… sekä 93 §, minkä ohella 48 §:n 1 momentin
    ruotsinkielinen sanamuoto muutetaan, …``): the ``minkä ohella`` ("in addition
    to which") connective introduces a further amendment target that the old
    ``_target`` reaches past the WORD lead-in and keeps as a section ref. This
    driver's section continuation cannot skip the WORD lead-in, so it would
    silently drop the kept section — decline instead.
    """
    toks = scan.cur.tokens
    n = len(toks)
    i = scan.pos
    while i < n and toks[i].cat in ("COMMA", "CONJ", "SEKA", "DASH"):
        i += 1
    if not (
        i + 1 < n
        and toks[i].cat == "WORD"
        and (toks[i].text or "").lower() == "minkä"
        and toks[i + 1].cat == "WORD"
        and (toks[i + 1].text or "").lower() == "ohella"
    ):
        return False
    # The connective must lead into a structural section ref (the kept target).
    for k in range(i + 2, min(i + 6, n)):
        if toks[k].cat == "PYKALA":
            return True
        if toks[k].cat not in ("NUM", "LETTER", "DASH", "WORD"):
            break
    return False


def _skip_minka_ohella_leadin(scan: _Scan) -> bool:
    """Consume a ``minkä ohella`` connective leading into a section ref.

    The ``minkä ohella`` ("in addition to which") connective introduces a further
    amendment target the old ``_target`` reaches past the two-WORD lead-in and
    keeps as a section ref (``… sekä 93 §, minkä ohella 48 §:n 1 momentti
    muutetaan``). The section continuation cannot skip the bare-WORD lead-in
    itself, so the driver consumes exactly ``WORD("minkä") WORD("ohella")`` here —
    ONLY when it is immediately followed (within a few tokens) by a structural
    section ref — and re-enters target recognition at the section.

    Called with the cursor at the position the separator already advanced past
    (``after_sep``). Returns True (cursor left on the section ref's first token)
    iff the ``minkä ohella`` lead-in was present and consumed; otherwise leaves
    the cursor untouched. Reuses ``_tail_starts_with_minka_ohella_arm``'s shape
    test (run from the current cursor) so the consume and the decline guard agree
    by construction.
    """
    toks = scan.cur.tokens
    n = len(toks)
    i = scan.pos
    if not (
        i + 1 < n
        and toks[i].cat == "WORD"
        and (toks[i].text or "").lower() == "minkä"
        and toks[i + 1].cat == "WORD"
        and (toks[i + 1].text or "").lower() == "ohella"
    ):
        return False
    # Require the connective to lead into a structural section ref (the kept
    # target) — the same cue ``_tail_starts_with_minka_ohella_arm`` gates on.
    leads_to_section = False
    for k in range(i + 2, min(i + 6, n)):
        if toks[k].cat == "PYKALA":
            leads_to_section = True
            break
        if toks[k].cat not in ("NUM", "LETTER", "DASH", "WORD"):
            break
    if not leads_to_section:
        return False
    scan.goto(i + 2)
    return True


def _section_tail_carries_kept_content(
    scan: _Scan, verb: Optional[SourceVerb] = None
) -> bool:
    """True iff the dropped SECTION/CONTAINER tail holds nodes the old parser
    keeps or leaks — so the driver must decline rather than silently truncate.

    Unions the faithful keep/leak detectors over the dropped tail: a prov
    re-mention the old parser leaks, a trailing appendix arm it keeps, a dropped
    ``uusi`` insert arm it keeps, and a ``minkä ohella <sec> … muutetaan`` arm it
    keeps. (The heading-change arm is handled separately by
    ``_section_continuation_is_kept``.)

    ``verb`` is the verb-group verb. The ``uusi``-insert-arm detector fires ONLY
    under a ``lisätään`` (LISATA) verb: there every ``N §:ään uusi …`` arm is a
    kept operative insert the old parser folds in, whereas under ``muutetaan`` a
    trailing ``uusi …`` arm is benign residue the old section path also drops
    (gating on the verb avoids over-declining ~110 such MUUTTAA clauses).
    """
    if (
        verb == SourceVerb.LISATA
        and _tail_starts_with_insertion_arm(scan)
    ):
        return True
    return (
        _prov_rementtion_leaks(scan)
        or _tail_starts_with_appendix_arm(scan)
        or _tail_starts_with_heading_arm(scan)
        or _tail_starts_with_minka_ohella_arm(scan)
        or _tail_starts_with_rementioned_section_arm(scan)
        or _tail_starts_with_second_statute_arm(scan)
        or _tail_starts_with_part_scoped_arm(scan)
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


def _skip_named_row_residue(scan: _Scan) -> bool:
    """Consume a post-target named-row residue like ``koodi 121`` (old sp:3652).

    The structural target parser stops before the row-designator tail (``koodi
    <num>``); a mixed clause continues with additional ordinary targets after it.
    Without this skip the continuation loop would treat ``koodi 121`` as an
    undecodable tail and drop every later target. Faithful to the old
    ``_skip_named_row_residue``: only consumes a ``koodi`` WORD followed by at
    least one NUM / LETTER / DASH; otherwise leaves the cursor untouched.
    """
    saved = scan.pos
    t = scan.peek()
    if not (t and t.cat == "WORD" and (t.text or "").lower() == "koodi"):
        return False
    scan.advance()
    saw_code = False
    while (t := scan.peek()) and t.cat in ("NUM", "LETTER", "DASH"):
        scan.advance()
        saw_code = True
    if saw_code:
        return True
    scan.goto(saved)
    return False


def _skip_heading_residue(
    scan: _Scan, *, allow_section_destination_residue: bool = False
) -> bool:
    """Consume a bare heading-placement residue left after provenance tagging
    (old sp:3560): ``uusi [otsikko | <luku> otsikko | <num> luvun otsikko]``.

    A trailing heading-placement arm whose payload the section family cannot mint
    into a node is swallowed by the old outer loop. Reproduced so the continuation
    loop skips it and keeps reaching later targets rather than declining.
    """
    saved = scan.pos
    if not ((t := scan.peek()) and t.cat == "UUSI"):
        return False
    scan.advance()
    t = scan.peek()
    if t and t.cat == "OTSIKKO":
        scan.advance()
        return True
    if t and t.cat == "LUKU" and t.case == "GEN":
        scan.advance()
        if (t := scan.peek()) and t.cat == "OTSIKKO":
            scan.advance()
            return True
        scan.goto(saved)
        return False
    if t and t.cat == "NUM":
        scan.advance()
        if (t := scan.peek()) and t.cat == "LETTER":
            scan.advance()
        if (
            allow_section_destination_residue
            and (t := scan.peek())
            and t.cat == "PYKALA"
            and t.case == "GEN"
        ):
            scan.advance()
            if (t := scan.peek()) and t.cat == "EDELLA":
                scan.advance()
                return True
            scan.goto(saved)
            return False
        if (t := scan.peek()) and t.cat == "LUKU" and t.case == "GEN":
            scan.advance()
            if (t := scan.peek()) and t.cat == "OTSIKKO":
                scan.advance()
                return True
    scan.goto(saved)
    return False


def _skip_anaphoric_heading_residue(scan: _Scan) -> bool:
    """Consume an anaphoric heading-placement residue, minting no node.

    Matches ``[<anaphor>] (edellä|edelle) uusi [N luvun] (väli|ala)otsikko`` — the
    ``[uusi N §] ja sen edelle uusi väliotsikko`` tail. The old parser consumes
    this arm but represents NO heading node for the anaphoric form (verified
    byte-identical: a single SECTION insertion node with the whole clause
    consumed), so the new outer loop swallows it the same way.

    Position-gated: the ENTIRE ``[anaphor] EDELLA uusi [N luvun] OTSIKKO`` shape
    must match or the cursor is rewound, so a stray ``WORD`` / ``EDELLA`` is never
    swallowed. The non-anaphoric ``N §:n edelle uusi väliotsikko`` form (a §:GEN
    target before EDELLA) does not match here.
    """
    saved = scan.pos
    # Optional anaphor pronoun (``sen`` / ``niiden`` / …) directly before EDELLA.
    if (t := scan.peek()) and t.cat == "WORD":
        nxt = scan.peek(1)
        if nxt is not None and nxt.cat == "EDELLA":
            scan.advance()
    if not ((t := scan.peek()) and t.cat == "EDELLA"):
        scan.goto(saved)
        return False
    scan.advance()  # edellä / edelle
    if not ((t := scan.peek()) and t.cat == "UUSI"):
        scan.goto(saved)
        return False
    scan.advance()
    # Optional ``N luvun`` chapter-genitive qualifier before the heading noun.
    saved_q = scan.pos
    if (t := scan.peek()) and t.cat == "NUM":
        scan.advance()
        if (t := scan.peek()) and t.cat == "LETTER":
            scan.advance()
        if (t := scan.peek()) and t.cat == "LUKU" and t.case == "GEN":
            scan.advance()
        else:
            scan.goto(saved_q)
    elif (t := scan.peek()) and t.cat == "LUKU" and t.case == "GEN":
        scan.advance()
    if (t := scan.peek()) and t.cat in ("OTSIKKO", "VALIOTSIKKO"):
        scan.advance()
        # A terminal anaphoric heading is the benign consume-and-drop form. The
        # same residue may also be followed by a list separator and another clean
        # insertion arm (``… 9 b § ja sen edelle uusi 2 a luvun otsikko sekä
        # asetukseen uusi 118 b §``); leave the separator for the outer loop.
        # Other trailing content (``, jolloin …`` renumber tail, a cross-verb
        # continuation) belongs to a complex clause the new parser must still
        # decline (1999/1001) — rewind so it is not silently swallowed.
        nxt = scan.peek()
        if nxt is None or nxt.cat in ("END_SENTINEL_SPAN", "COMMA", "CONJ", "SEKA"):
            return True
        scan.goto(saved)
        return False
    scan.goto(saved)
    return False


def _try_current_section_renumber_tail(
    scan: _Scan,
    verb: SourceVerb,
    chapter: str,
    part: str,
) -> list[SurfaceNode] | None:
    """Parse SIIRTAA ``nykyinen N § uudeksi M §:ksi`` section relabel tails."""
    if verb is not SourceVerb.SIIRTAA:
        return None
    saved = scan.pos
    start = scan.pos

    current_word = scan.peek()
    if not (
        current_word
        and current_word.cat == "WORD"
        and (current_word.lemma or current_word.text).lower() in {"nykyinen", "nykyiset"}
    ):
        return None
    scan.advance()

    source_labels = _number_list(scan)
    if not source_labels:
        scan.goto(saved)
        return None
    if not ((source_pykala := scan.peek()) and source_pykala.cat == "PYKALA"):
        scan.goto(saved)
        return None
    scan.advance()

    new_word = scan.peek()
    if not (
        new_word
        and new_word.cat == "WORD"
        and (new_word.lemma or new_word.text).lower() in {"uudeksi", "uusiksi"}
    ):
        scan.goto(saved)
        return None
    scan.advance()

    destination_labels = _number_list(scan)
    if not destination_labels:
        scan.goto(saved)
        return None
    if not ((destination_pykala := scan.peek()) and destination_pykala.cat == "PYKALA"):
        scan.goto(saved)
        return None
    scan.advance()

    if len(source_labels) != len(destination_labels):
        scan.goto(saved)
        return None

    witness = SurfaceWitness(
        rule_id="fi.current_section_renumber_tail",
        source_span=(start, scan.pos),
    )
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


def _normalize_intrabatch_explicit_part_scope(
    nodes: list[SurfaceNode], inherited_part: str
) -> list[SurfaceNode]:
    """Retarget later nodes in one batch when an explicit part switch appears
    earlier in the same batch (faithful port of old sp:3272).

    A single batch can switch parts mid-list (``V osan 4 luvun …, VI osan otsikko,
    1-3 luvun …``); the running part context updates only after the whole batch is
    built, so later chapter/section descendants can carry the stale pre-switch
    part. When an explicit PART target precedes them, retarget them to the new
    part — dropping a section's stale inherited ``chapter`` (which belonged to the
    old part) so the address is not internally contradictory.
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


def _try_including_preceding_heading(
    scan: _Scan, chapter: str, part: str
) -> Optional[list[SurfaceNode]]:
    """``mukaanluettuna N §:n edellä olevan väliotsikon`` as a heading target
    (old sp:3590). The section-range arm gains a HEADING facet; the enclosing
    list continues past it. Rewinds on no match."""
    parsed = recognize_including_preceding_heading_target(scan, chapter, part)
    if parsed is None:
        return None
    return emit_headings_nodes(parsed, chapter=chapter, part=part)


def _try_recognize_target(
    scan: _Scan,
    batch_start: int,
    chapter: str,
    part: str,
    verb: Optional[SourceVerb] = None,
    started_with_citation_span: bool = False,
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
    # The old ``_target`` records whether this batch OPENED on a CITATION_SPAN —
    # the ``started_with_citation_span`` hint that lets the ``nojalla`` authority
    # skip fire on a citation-stamped authority list. The flag may be supplied by
    # the verb-group level (the leading citation was consumed before this call) OR
    # detected at this batch's own first token (a continuation arm opening on one).
    started_with_citation_span = started_with_citation_span or (
        batch_start < len(scan.cur.tokens)
        and scan.cur.tokens[batch_start].cat == "CITATION_SPAN"
    )
    try:
        parsed_ins = recognize_insertion(
            scan, chapter, part, verb, started_with_citation_span=started_with_citation_span
        )
    except OutOfScopeInsertion as exc:
        # The recogniser identified an out-of-scope insertion shape (e.g. a
        # ``… nojalla uusi 8 b`` bare-section authority insert). Decline the
        # whole clause rather than mis-read the authority list as a section ref.
        raise OutOfScope(str(exc)) from exc
    if parsed_ins is not None:
        nodes = emit_insertion_nodes(parsed_ins)
        return _stamp_insertion_batch(nodes, batch_start, scan.pos), "insertion"

    scan.goto(start)
    parsed_hp = recognize_trailing_heading_placement(scan, chapter, part)
    if parsed_hp is not None:
        return emit_headings_nodes(parsed_hp, chapter=chapter, part=part), "heading"

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
    scan: _Scan,
    chapter: str = "",
    part: str = "",
    verb: Optional[SourceVerb] = None,
    *,
    started_with_citation_span: bool = False,
) -> tuple[list[SurfaceNode], FamilyKind]:
    """Recognize a single target (any wired structural family); emit nodes.

    Records the batch start (the old ``_target`` entry, before its own
    sentinel skip — the witness anchor for insertions), skips sentinel-span
    lead-in and an optional DOC:GEN ("lain 6, 7 ja 18 §"), then dispatches the
    families in old-``_target`` order. Returns ``(nodes, family_kind)``. Raises
    :class:`OutOfScope` if no target is found.

    ``verb`` is the verb-group verb, threaded to the insertion recognizer so its
    LISATA-only no-``uusi`` fallback arms can fire. ``started_with_citation_span``
    is the verb-group-level hint (the leading CITATION_SPAN was consumed before
    this call), threaded to the insertion recogniser's ``nojalla`` authority skip.
    """
    batch_start = scan.pos
    _skip_sentinels(scan)

    # Optional DOC:GEN before structural targets (the old _target skips it).
    doc_saved = scan.pos
    t = scan.peek()
    if t and t.cat == "DOC" and t.case == "GEN":
        scan.advance()
        _skip_sentinels(scan)

    result = _try_recognize_target(
        scan, batch_start, chapter, part, verb, started_with_citation_span
    )
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


def _try_chapter_subheading(
    scan: _Scan, chapter: str, part: str
) -> Optional[list[SurfaceNode]]:
    """Recognize a mid-list chapter-scoped labelled subheading continuation arm.

    A ``, D väliotsikko,`` / ``, alaluvun C otsikko,`` arm inside an established
    chapter-scoped list inherits the running ``chapter`` scope (``6 luvun C
    väliotsikko, 14–18 §, D väliotsikko, …``). Emits a CHAPTER HEADING node for
    the inherited chapter, label-discriminated by the subheading letter, so the
    chapter scope continues into the following section arms. Returns ``None``
    when no inherited chapter scope is active or the shape does not match.
    """
    parsed = recognize_chapter_scoped_subheading(scan, chapter)
    if parsed is None:
        return None
    return emit_containers_nodes(parsed, chapter=chapter, part=part)


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


def _try_part_backref(
    scan: _Scan, chapter: str, part: str
) -> Optional[list[SurfaceNode]]:
    """Recognize a ``mainitun osan <section_ref | chapter_ref>`` resumption arm.

    Faithful to the old ``_parse_part_backref_target``: only legal once a part has
    been named (the ``if not part`` guard), it resumes that part and names new
    scoped section (or part-scoped chapter) targets under it. The inner reference
    carries its own witness id; the BACKREF + OSA prefix carries no node. Returns
    None (rewinding) on no match or no resumed part.
    """
    if not part:
        return None
    saved = scan.pos
    parsed = recognize_part_backref(scan, part=part)
    if parsed is None:
        scan.goto(saved)
        return None
    return emit_part_backref_nodes(parsed, chapter=chapter, part=part)


def _try_chapter_backref(
    scan: _Scan, chapter: str, part: str
) -> Optional[list[SurfaceNode]]:
    """Recognize a ``mainitun luvun <section_ref>`` resumption arm.

    Faithful to the old ``_parse_chapter_backref_target``: only legal once a
    chapter has been named (the ``if not chapter`` guard), it resumes that chapter
    and names new scoped section targets under it (inheriting the part context).
    The inner section carries its own witness id; the BACKREF + LUKU prefix
    carries no node. Returns None (rewinding) on no match or no resumed chapter.
    """
    if not chapter:
        return None
    saved = scan.pos
    parsed = recognize_chapter_backref(scan)
    if parsed is None:
        scan.goto(saved)
        return None
    return emit_chapter_backref_nodes(parsed, chapter=chapter, part=part)


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


# Structural nouns an anaphoric determiner may point at (old sp:3929): a section /
# momentti for an intra-group sub-target anaphora, or a document / chapter / part
# for a (re)anchored root / chapter-scoped insert.
_ANAPHORIC_DETERMINER_NOUNS = frozenset({"PYKALA", "MOMENTTI", "DOC", "LUKU", "OSA"})


def _stamp_anaphoric_determiner_witness(
    nodes: Sequence[SurfaceNode], start: int, end: int
) -> list[SurfaceNode]:
    """Stamp the ``fi.anaphoric_determiner_insert`` witness across the arm.

    Faithful to the old dispatch (sp:4217-4227): one shared witness spanning the
    whole arm (``start`` = the loop-iteration separator, ``end`` = the cursor),
    rule_id ``fi.anaphoric_determiner_insert`` for every emitted insertion — NOT
    the per-shape ``fi.insertion_*`` the insertion batch witness would infer.
    """
    out: list[SurfaceNode] = []
    for node in nodes:
        if not isinstance(node, SurfaceInsertion):
            raise TypeError(f"expected SurfaceInsertion, got {type(node).__name__}")
        out.append(
            SurfaceInsertion(
                kind=node.kind,
                label=node.label,
                chapter=node.chapter,
                part=node.part,
                sub_target=node.sub_target,
                witness=SurfaceWitness(
                    rule_id="fi.anaphoric_determiner_insert", source_span=(start, end)
                ),
            )
        )
    return out


def _stamp_anaphoric_insertion_witness(
    nodes: Sequence[SurfaceNode], rule_id: str, start: int, end: int
) -> list[SurfaceNode]:
    out: list[SurfaceNode] = []
    for node in nodes:
        if not isinstance(node, SurfaceInsertion):
            raise TypeError(f"expected SurfaceInsertion, got {type(node).__name__}")
        out.append(
            SurfaceInsertion(
                kind=node.kind,
                label=node.label,
                chapter=node.chapter,
                part=node.part,
                sub_target=node.sub_target,
                witness=SurfaceWitness(rule_id=rule_id, source_span=(start, end)),
            )
        )
    return out


def _resolve_anaphoric_sub_nodes(
    parsed: ParsedAnaphoricSubTarget, prev_sec: str, prev_ch: str, prev_mom: int
) -> list[SurfaceNode]:
    """Fill the placeholder section/chapter/momentti on a bare-anaphoric arm.

    The recognizer emitted ``InsNode``s with placeholder section/chapter (and, for
    the ``momenttiin`` reading, a placeholder host momentti). Resolve them against
    the intra-group anchor ``(prev_sec, prev_ch, prev_mom)``: every node takes the
    prior section / chapter; a ``momentti_from_context`` arm's kohta inserts (the
    item-bearing nodes) take the prior momentti as their host (``prev_mom or 1``,
    mirroring the old ``_insertion_sub_target`` ``eff_mom = mom_ctx or 1``).
    """
    out: list[SurfaceNode] = []
    for node in parsed.nodes:
        assert isinstance(node, InsNode)
        sub_target = None
        if node.sub_target is not None:
            st = node.sub_target
            momentti = st.momentti
            if parsed.momentti_from_context and st.item:
                momentti = prev_mom or 1
            sub_target = SurfaceSubRef(momentti=momentti, item=st.item, subitem=st.subitem, facet=st.facet)
        out.append(
            SurfaceInsertion(
                kind=node.kind,
                label=prev_sec,
                chapter=prev_ch,
                part=node.part,
                sub_target=sub_target,
            )
        )
    return out


def _try_bare_anaphoric_insert_continuation(
    scan: _Scan,
    sep_saved: int,
    chapter: str,
    verb: Optional[SourceVerb],
    group_nodes: list[SurfaceNode],
) -> Optional[list[SurfaceNode]]:
    """Resolve bare ``N momenttiin uusi ...`` / ``uusi ...`` insertion continuations."""
    if verb != SourceVerb.LISATA:
        return None
    saved = scan.pos
    res = _update_context_from_nodes(group_nodes, VerbGroupContext(chapter=chapter), verb)
    prev_sec = res.last_section
    prev_ch = res.last_section_chapter or chapter
    prev_mom = res.last_momentti
    if not prev_sec:
        return None

    parsed_sub = recognize_numbered_bare_anaphoric_momentti_insert(scan)
    rule_id = "fi.anaphoric_momentti_ill"
    if parsed_sub is None:
        scan.goto(saved)
        parsed_sub = recognize_bare_anaphoric_sub_target(scan)
        rule_id = "fi.anaphoric_bare_uusi"
    if parsed_sub is None:
        scan.goto(saved)
        return None
    if parsed_sub.momentti_from_context and not prev_mom:
        scan.goto(saved)
        return None
    sub_nodes = _resolve_anaphoric_sub_nodes(parsed_sub, prev_sec, prev_ch, prev_mom)
    return _stamp_anaphoric_insertion_witness(sub_nodes, rule_id, sep_saved, scan.pos)


def _try_anaphoric_determiner_insert(
    scan: _Scan,
    sep_saved: int,
    chapter: str,
    part: str,
    verb: Optional[SourceVerb],
    group_nodes: list[SurfaceNode],
) -> Optional[list[SurfaceNode]]:
    """Resolve a ``sanottuun/mainittuun/samaan …`` anaphoric-determiner insert arm.

    Faithful port of the old ``_parse_anaphoric_determiner_insert`` (sp:3897-4012),
    dispatched at sp:4212 only under a ``LISATA`` verb. The arm's target is named
    only by an anaphoric determiner pointing at a previously-mentioned unit:

      * ``sanottuun/samaan lakiin uusi N §`` (DOC) / ``… osaan …`` (OSA) → a root
        insert re-dispatched through ``recognize_insertion`` with NO inherited
        chapter (the ``lakiin`` re-anchors to statute level);
      * ``saman lain N lukuun uusi M §`` (LUKU) → a chapter-scoped insert, the
        chapter supplied by the ``N lukuun`` itself;
      * ``sanottuun pykälään …`` (PYKALA) / ``mainittuun momenttiin …`` (MOMENTTI)
        → an intra-group sub-target anaphora resolved against the last section /
        momentti mentioned in THIS group's accumulated nodes.

    The cursor sits on the determiner WORD. Stamps the distinct
    ``fi.anaphoric_determiner_insert`` witness across the arm. Returns the emitted
    nodes (cursor past the arm) or None (cursor restored) on any non-clean shape.
    """
    if verb != SourceVerb.LISATA:
        return None
    saved = scan.pos
    t = scan.peek()
    if t is None or t.cat != "WORD" or (t.text or "").lower() not in _ANAPHORIC_INSERT_DETERMINERS:
        return None
    nxt = scan.peek(1)
    if nxt is None or nxt.cat not in _ANAPHORIC_DETERMINER_NOUNS:
        return None

    # ``DOC`` / ``OSA`` → consume the determiner, re-dispatch through the insertion
    # recognizer. The determiner only needs consuming; a root insert must NOT
    # inherit the prior arm's chapter (the ``lakiin`` re-anchors to statute level),
    # so the inherited chapter is dropped (the ``OSA:ILL`` arm still threads the
    # inherited part).
    if nxt.cat in ("DOC", "OSA"):
        scan.advance()  # consume the determiner WORD
        try:
            parsed_ins = recognize_insertion(scan, "", part, verb)
        except OutOfScopeInsertion:
            scan.goto(saved)
            return None
        if parsed_ins is None:
            scan.goto(saved)
            return None
        ins_nodes = emit_insertion_nodes(parsed_ins)
        return _stamp_anaphoric_determiner_witness(ins_nodes, sep_saved, scan.pos)

    # ``LUKU`` → a bare ``[said] lukuun uusi M §`` chapter-scoped insert: the
    # number-less chapter is the inherited running chapter (old Pattern E). The
    # determiner is consumed, then the dedicated bare-chapter recognizer emits
    # placeholder-chapter nodes the driver fills with the inherited chapter.
    if nxt.cat == "LUKU":
        scan.advance()  # consume the determiner WORD
        bare_nodes = recognize_bare_anaphoric_chapter_insert(scan, verb)
        if bare_nodes is None:
            scan.goto(saved)
            return None
        ins_nodes = [
            SurfaceInsertion(
                kind=n.kind, label=n.label, chapter=chapter, part=part, sub_target=None
            )
            for n in bare_nodes
        ]
        return _stamp_anaphoric_determiner_witness(ins_nodes, sep_saved, scan.pos)

    # ``PYKALA`` / ``MOMENTTI`` → intra-group sub-target anaphora. Resolve the
    # anchor against THIS group's accumulated nodes (a fresh context carrying only
    # the running chapter, exactly as old sp:3933 passes ``VerbGroupContext(
    # chapter=chapter)`` — the determiner anaphora is intra-group, not cross-group).
    res = _update_context_from_nodes(group_nodes, VerbGroupContext(chapter=chapter), verb)
    prev_sec = res.last_section
    prev_ch = res.last_section_chapter or chapter
    prev_mom = res.last_momentti
    if not prev_sec:
        return None

    scan.advance()  # consume the determiner WORD
    parsed_sub = recognize_bare_anaphoric_sub_target(scan)
    if parsed_sub is None:
        scan.goto(saved)
        return None
    # The bare ``momenttiin`` reading needs a prior momentti to host the kohta.
    if parsed_sub.momentti_from_context and not prev_mom:
        scan.goto(saved)
        return None
    sub_nodes = _resolve_anaphoric_sub_nodes(parsed_sub, prev_sec, prev_ch, prev_mom)
    return _stamp_anaphoric_determiner_witness(sub_nodes, sep_saved, scan.pos)


def _try_cross_verb_anaphoric_insert(
    scan: _Scan, ctx: VerbGroupContext, verb: Optional[SourceVerb]
) -> Optional[list[SurfaceNode]]:
    """Resolve a cross-verb-group anaphoric insert against the DISCOURSE context.

    Faithful port of the old ``_verb_group`` anaphoric fallback (sp:4942-5028):
    when a LISATA group's first target is named only by reference to a section a
    *prior* verb group established (``muutetaan … 5 § … sekä lisätään sanottuun
    pykälään uusi 3 momentti`` — the determiner lexed away into a sentinel span),
    the host section is ``ctx.last_section`` (the cross-group anchor), NOT a node
    in this group. The intra-group ``_try_anaphoric_determiner_insert`` cannot
    reach it (it resolves against THIS group's accumulated nodes).

    Only fires under LISATA with a non-empty ``ctx.last_section`` (the old fallback
    guard ``if not nodes and ctx.last_section``). The cursor sits at the group's
    post-sentinel first-target position. Stamps the old fallback's witness
    (``fi.cross_verb_momentti`` / ``fi.cross_verb_bare_uusi``) spanning the arm.
    Returns the emitted nodes (cursor past the arm) or None (cursor restored).
    """
    if verb != SourceVerb.LISATA or not ctx.last_section:
        return None
    saved = scan.pos
    parsed = recognize_cross_verb_anaphoric_insert(scan, ctx.last_momentti)
    if parsed is None:
        scan.goto(saved)
        return None
    rule_id = (
        "fi.cross_verb_momentti"
        if parsed.host_from_momentti
        else "fi.cross_verb_bare_uusi"
    )
    span = (parsed.span.start, parsed.span.end)
    out: list[SurfaceNode] = []
    for node in parsed.nodes:
        # The recognizer only returns SUB-TARGET inserts (momentti / kohta into the
        # prior section), so every node carries the resolved section / chapter.
        assert node.sub_target is not None
        out.append(
            SurfaceInsertion(
                kind=node.kind,
                label=ctx.last_section,
                chapter=ctx.last_section_chapter,
                part=node.part,
                sub_target=SurfaceSubRef(
                    momentti=node.sub_target.momentti,
                    item=node.sub_target.item,
                    subitem=node.sub_target.subitem,
                    facet=node.sub_target.facet,
                ),
                witness=SurfaceWitness(rule_id=rule_id, source_span=span),
            )
        )
    return out


# Structural-anchor token categories: their presence in a verb group's span means
# the old ``_target_list`` would anchor a target there, so the group is NOT
# genuinely empty even when the wired families decline its first target.
_STRUCTURAL_ANCHOR_CATS = frozenset(
    {"PYKALA", "LUKU", "OSA", "MOMENTTI", "KOHTA", "NIMIKE", "OTSIKKO", "LIITE"}
)


def _group_span_has_structural_anchor(scan: _Scan) -> bool:
    """True if a structural-anchor token sits before the next VERB / end.

    Used to distinguish a genuinely-empty verb group (an un-modelled named
    provision the old parser also yields nothing for) from one whose targets the
    wired families merely cannot reproduce (which must decline, not drop).
    """
    toks = scan.cur.tokens
    for i in range(scan.pos, len(toks)):
        if toks[i].cat == "VERB":
            return False
        if toks[i].cat in _STRUCTURAL_ANCHOR_CATS:
            return True
    return False


# Lead-in cats the old ``_target`` skips before its first structural target (the
# sentinel-span skip + an optional DOC re-anchor) — walked past when testing
# whether a verb group OPENS on an un-modellable named provision.
_NAMED_PROV_LEADIN_CATS = frozenset(
    {"TEMPORAL", "DOC"}
) | _SENTINEL_SPAN_CATS


def _group_opens_on_named_provision(scan: _Scan) -> bool:
    """True iff the verb group OPENS on a ``N §[:n/:ään] <word> …`` named provision.

    The old ``_target_list`` calls ``_target`` once at the first position and, on
    failure, returns ``[]`` (sp:4093) — it never scans ahead for a later ``§``.
    So a verb group whose FIRST target is an un-modellable named sub-provision
    (``N §:ään sisältyvä … ryhmä``, ``N §:n … koskeva nimike``, ``N § näin
    kuuluvaksi``) is dropped WHOLE by the old parser, even though a structural
    ``§`` from a later (also-dropped) arm sits in its span. The generic
    structural-anchor safety net over-declines those: this narrows it.

    The cue is a section anchor ``[NUM/LETTER/DASH]* PYKALA`` immediately followed
    by a bare ``WORD`` (the named-provision descriptor) before any list separator
    or structural sub-noun — a shape the old ``_target`` cannot anchor (the
    section recognizer needs a structural sub-ref, a sentinel, or a separator
    after the ``§``, never a bare descriptive WORD). A ``PYKALA`` followed by an
    ``EDELLA`` (``N §:n edellä oleva väliotsikko``) or by a structural sub-noun
    (``§:n N momentti``) is NOT matched — those the old parser keeps.
    """
    toks = scan.cur.tokens
    n = len(toks)
    i = scan.pos
    while i < n and toks[i].cat in _NAMED_PROV_LEADIN_CATS:
        i += 1
    # An optional leading list separator (the old first-target leading-sep retry).
    if i < n and toks[i].cat in ("COMMA", "CONJ", "SEKA", "DASH"):
        i += 1
        while i < n and toks[i].cat in _NAMED_PROV_LEADIN_CATS:
            i += 1
    j = i
    while j < n and toks[j].cat in ("NUM", "LETTER", "DASH"):
        j += 1
    # Require a real number run before the section (a bare ``§`` with no number is
    # not a section anchor the old parser keys a named provision on).
    if j == i or j >= n or toks[j].cat != "PYKALA":
        return False
    k = j + 1
    return k < n and toks[k].cat == "WORD"


def _recognize_first_target_or_empty(
    scan: _Scan,
    verb: Optional[SourceVerb] = None,
    *,
    started_with_citation_span: bool = False,
) -> Optional[tuple[list[SurfaceNode], FamilyKind]]:
    """Recognize a verb group's first target, or ``None`` for an empty group.

    Mirrors the old ``_target_list`` first-target acquisition (sp:4036-4093): the
    bare ``_target`` recognizer first, then — on failure — a single leading
    ``ja``/``sekä`` separator skip and a re-attempt (``muutetaan [CITE] ja N §``).
    Only the ``not a target at target position`` failure (no structural target)
    falls through to the leading-separator retry / empty result; any other
    out-of-scope shape propagates so the driver still declines loudly.

    Returns ``(nodes, family_kind)`` on success, or ``None`` when even the
    leading-separator retry finds no target — in which case the cursor is left at
    the post-sentinel position (where the un-modelled target sits) so the caller
    can return an empty group and ``parse()`` resumes the verb-seeking skip.
    """
    try:
        return _recognize_one_target(
            scan, verb=verb, started_with_citation_span=started_with_citation_span
        )
    except OutOfScope as exc:
        if str(exc) != "not a target at target position":
            raise
    # ``_recognize_one_target`` rewound to the post-sentinel position. Try a
    # single leading separator (sp:4088-4090) then re-attempt the first target.
    after_decline = scan.pos
    if _sep(scan) is not None:
        try:
            return _recognize_one_target(
                scan, verb=verb, started_with_citation_span=started_with_citation_span
            )
        except OutOfScope as exc:
            if str(exc) != "not a target at target position":
                raise
    # No target even after a leading separator. The group is droppable as empty
    # ONLY when the old parser would ALSO recognize nothing here. The old
    # ``_target_list`` returns ``[]`` precisely when it can anchor no structural
    # target in the span up to the next VERB — so when a structural anchor token
    # (``§`` / ``luku`` / ``osa`` / ``momentti`` / ``kohta`` / ``nimike`` /
    # ``otsikko`` / ``liite``) DOES sit in this group's span, the old parser
    # recognizes a target the wired families cannot reproduce (a bare-number
    # ``lakiin N a §`` insert, a ``N §:n edellä oleva väliotsikko`` heading-
    # before-section, …). Dropping that group would silently lose a verb group
    # the old parser keeps — so DECLINE loudly instead.
    scan.goto(after_decline)
    # A group that OPENS on an un-modellable named provision (``N §:ään sisältyvä
    # … ryhmä``, ``N §:n … koskeva nimike``, ``N § näin kuuluvaksi``) is dropped
    # WHOLE by the old ``_target_list`` (its ``_target`` fails at the first
    # position and it returns ``[]`` without scanning ahead). The generic
    # structural-anchor net would over-decline it on a later ``§`` in its span —
    # drop the group instead, mirroring the old parser.
    if _group_opens_on_named_provision(scan):
        return None
    if _group_span_has_structural_anchor(scan):
        raise OutOfScope("not a target at target position")
    return None


def _parse_verb_group(
    scan: _Scan,
    ctx: VerbGroupContext,
    jolloin_renumber_pairs: dict[int, list[tuple[str, str, str]]] | None = None,
    consumed_jolloin_positions: list[int] | None = None,
    consumed_jolloin_contexts: dict[int, tuple[str, str]] | None = None,
) -> tuple[Optional[SourceVerb], list[SurfaceNode], VerbGroupContext]:
    """Parse one verb group: VERB then a separator-joined structural-target list.

    Returns ``(verb_code, nodes, ctx)`` where ``ctx`` is the incoming discourse
    state advanced over this group's nodes (the anchors a later group's anaphoric
    arm resolves against). Raises :class:`OutOfScope` on any shape inside the
    group this driver does not reproduce.

    The incoming ``ctx`` carries the cross-verb-group last-section / last-momentti
    anchors plus the running chapter/part scope; intra-group scope is threaded via
    the local ``chapter`` / ``part`` exactly as before (behaviour-neutral) and the
    returned ``ctx`` is computed from the group's accumulated nodes.

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

    # Old ``_target_list`` hint: did this verb group OPEN on a CITATION_SPAN (one
    # the sentinel skip is about to consume)? It enables the first target's
    # ``nojalla`` authority-basis skip on a citation-stamped authority list.
    group_started_with_citation_span = (
        scan.pos < len(scan.cur.tokens)
        and scan.cur.tokens[scan.pos].cat == "CITATION_SPAN"
    )

    _skip_sentinels(scan)
    _skip_cat(scan, "TEMPORAL")

    # A SIIRTAA (siirretään) group may be a cross-verb move retarget
    # (``[muutettu] N § M lukuun``) or a relabel-from-context
    # (``[N luvun] M §:ksi``). Both bind to a section established in a *preceding*
    # verb group / the discourse context — cross-verb-group resolution this
    # driver does not perform. Decline rather than mis-read the move's source
    # section as an operative target.
    #
    # A leading destination-part prefix (``I osaan, ...``) on a SIIRTAA group is
    # NOT a cross-verb form: it supplies the move destination part for the targets
    # that follow; it is consumed here (not parsed as an operative target) and
    # threaded onto every resulting target ref as ``renumber_dest_part``
    # (old sp:4912/4921-4939).
    move_dest_part = ""
    if verb == SourceVerb.SIIRTAA:
        saved_move = scan.pos
        if recognize_cross_verb_move_tail(scan) is not None:
            raise OutOfScope("cross-verb move retarget (cross-verb-group resolution)")
        scan.goto(saved_move)
        if recognize_relabel_from_context(scan) is not None:
            raise OutOfScope("relabel from context (cross-verb-group resolution)")
        scan.goto(saved_move)
        leading_part = recognize_leading_move_destination_part(scan)
        if leading_part is not None:
            move_dest_part = leading_part.destination_part

    # An *empty* verb group: this verb names a target none of the wired families
    # recognizes (an un-modelled named provision such as ``soveltamissäännöksen
    # N momentti`` / ``N §:ään sisältyvä … ryhmä``, a heading-before-section
    # ``N §:n edellä oleva väliotsake``, …). The old parser's ``_target_list``
    # returns ``[]`` for these and the outer loop DROPS the group (old sp:5107 /
    # sp:5154), then advances to the next VERB to parse the following group(s).
    # Mirror that: return ``(verb, [])`` WITHOUT consuming so ``parse()`` resumes
    # the verb-seeking skip from here, reproducing the old grouping rather than
    # raising and falling back to the old parser wholesale.
    #
    # Before declaring the group empty, mirror the old ``_target_list`` leading
    # fallback (sp:4085-4090): a verb group may open with a stray leading
    # ``ja``/``sekä`` separator (``muutetaan [CITE] ja N §``) — skip one such
    # separator and re-attempt the first target so the group is recognized, not
    # spuriously dropped.
    first_batch_start = scan.pos
    try:
        batch_kind = _recognize_first_target_or_empty(
            scan, verb, started_with_citation_span=group_started_with_citation_span
        )
    except OutOfScope:
        # The wired families declined this group's first target. Before propagating
        # the decline, try the cross-verb-group anaphoric fallback: a LISATA group
        # whose host section is established in a PRIOR verb group (``… 5 § … sekä
        # lisätään sanottuun pykälään uusi 3 momentti``). The old ``_verb_group``
        # reaches this only after ``_target_list`` returns ``[]`` (it never raises),
        # so this driver must recover here rather than fall through to the decline.
        scan.goto(first_batch_start)
        cross_nodes = _try_cross_verb_anaphoric_insert(scan, ctx, verb)
        if cross_nodes is None:
            raise
        new_ctx = _update_context_from_nodes(cross_nodes, ctx, verb)
        return verb, cross_nodes, new_ctx
    if batch_kind is None:
        # An empty group: the wired families found no target. The old ``_verb_group``
        # likewise reaches the anaphoric fallback here (``if not nodes and
        # ctx.last_section``) — try the cross-verb anaphoric insert before dropping
        # the group as empty.
        empty_pos = scan.pos
        scan.goto(first_batch_start)
        cross_nodes = _try_cross_verb_anaphoric_insert(scan, ctx, verb)
        if cross_nodes is not None:
            new_ctx = _update_context_from_nodes(cross_nodes, ctx, verb)
            return verb, cross_nodes, new_ctx
        # No cross-verb arm: leave the cursor where ``_recognize_first_target_or_empty``
        # left it (the post-sentinel un-modelled-target position ``parse()`` resumes
        # the verb-seeking skip from), exactly as before.
        scan.goto(empty_pos)
        return verb, [], ctx
    batch, kind = batch_kind
    # An authority-basis citation mis-read as the first target: ``muutetaan …
    # kielilain 25 §:n nojalla, … asetuksen 1, 2, 5, 8 ja 9 §`` — the ``25 §:n
    # nojalla`` ("by virtue of §25") is the LEGAL BASIS for the amendment, not an
    # operative target; the real targets follow behind a BARE statute name (``…
    # nojalla [date] annetun kansaneläkeasetuksen 80 ja 81 §``). The section
    # recognizer reads ``99 §`` and stops at ``nojalla``; the mis-read authority
    # section must be DISCARDED and the real bare-name target list recovered.
    #
    # Recover natively: skip the leading ``nojalla`` authority lead-in to the real
    # target start and re-recognize the operative batch there. The mis-read
    # authority batch (``99 §``) is dropped, so the recovered nodes replace it. On
    # a fuzzy shape the skip declines and the original decline below still fires —
    # the old parser then owns the clause (its ``_skip_authority_nojalla_lead_in``
    # handles the comma-jump forms ``25 §:n nojalla, … asetuksen N §`` correctly).
    if (
        (nojalla := scan.peek()) is not None
        and nojalla.cat == "WORD"
        and (nojalla.text or "").lower() == "nojalla"
        and _has_operative_target_before_verb(scan, scan.pos + 1)
    ):
        recovery_start = scan.pos
        if _skip_leading_nojalla_authority(scan):
            target_start = scan.pos
            try:
                rec_batch, rec_kind = _recognize_one_target(scan, "", "", verb)
            except OutOfScope:
                scan.goto(recovery_start)
            else:
                # Drop the mis-read authority section; the recovered bare-name
                # target list is the real batch. Re-stamp the first-batch start so
                # downstream scope / continuation reads from the recovered targets.
                batch, kind = rec_batch, rec_kind
                first_batch_start = target_start
        if scan.pos == recovery_start:
            raise OutOfScope("authority-basis nojalla citation mis-read as target")
    nodes = list(batch)
    last_batch: list[SurfaceNode] = list(batch)
    # Intra-group scope carry-forward: a later bare section list inherits the
    # preceding "N luvun" / "N osan" scope (the old parser threads this between
    # target batches in one verb group). A DOC:ILL re-anchor in the batch resets
    # the chapter to statute root (old sp:4102).
    chapter = _chapter_after_batch(scan, first_batch_start, batch, "", verb)
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
            # advanced), still tries another target at the new position — so
            # targets separated only by a provenance / citation span stay in the
            # list. Reproduce that for container continuations: if sentinels
            # advanced us and another container or section target follows, fold
            # it in and continue.
            if kind == "container" and scan.pos != saved:
                try:
                    more, more_kind = _recognize_one_target(scan, chapter, part)
                except OutOfScope:
                    scan.goto(saved)
                else:
                    if more_kind in {"container", "section"}:
                        nodes.extend(more)
                        last_batch = list(more)
                        chapter = _chapter_after_batch(scan, saved, more, chapter, verb)
                        part = _extract_part(more, part)
                        continue
                    scan.goto(saved)
            # An ``mukaanluettuna N §:n edellä olevan väliotsikon`` included
            # preceding-heading arm directly follows a section range (no
            # separator); the old ``_target_list`` folds it in and keeps parsing
            # later arms (sp:4117). Fold it and continue.
            scan.goto(saved)
            inc_nodes = _try_including_preceding_heading(scan, chapter, part)
            if inc_nodes is not None:
                nodes.extend(inc_nodes)
                last_batch = list(inc_nodes)
                chapter = _extract_chapter(inc_nodes, chapter, verb)
                part = _extract_part(inc_nodes, part)
                continue
            # A named-row residue (``koodi 121``) or a bare heading-placement
            # residue (``uusi [luvun] otsikko``) the section family stops before:
            # the old outer loop skips it and continues reaching later targets
            # (sp:3652 / sp:3560). Skip and re-enter the loop so the following
            # ``, N §`` separator continuation is reached rather than dropped.
            if kind in ("section", "container") and (
                _skip_named_row_residue(scan)
                or _skip_heading_residue(
                    scan,
                    allow_section_destination_residue=verb is SourceVerb.SIIRTAA,
                )
            ):
                continue
            # A trailing anaphoric heading-placement residue (``[sen] edellä uusi
            # [N luvun] (väli|ala)otsikko``) the old parser consumes but mints no
            # node for — e.g. ``lisätään lakiin uusi 29 a § ja sen edelle uusi
            # väliotsikko``. The old outer loop swallows it for any batch kind, so
            # skip it here (including for an INSERTION batch) and keep reaching
            # later targets rather than declining. The non-anaphoric ``N §:n
            # edelle …`` form (an explicit §:GEN target before EDELLA) is NOT
            # matched here — it stays declined so the old parser, which emits a
            # real heading node for it, is used, preserving parity.
            if _skip_anaphoric_heading_residue(scan):
                continue
            if kind == "insertion" and _skip_tilalle_uusi_tail(scan):
                continue
            # The old parser otherwise ends the target list here and lets the
            # outer loop swallow the tail. For an INSERTION batch a structural
            # tail the old parser keeps folding into the same list (chained
            # ``sekä uusi …`` arms) means ending here drops nodes — decline. For
            # a CONTAINER batch a ``näistä/niistä`` provenance back-ref
            # re-introduces trailing appendix / table arms the old parser keeps —
            # decline. A SECTION batch legitimately reads a trailing out-of-family
            # arm as residue the old section path also drops — do not fail-loud.
            if kind == "insertion" and not _tail_is_benign(scan):
                raise OutOfScope("undecodable insertion tail (no separator)")
            if kind == "container" and _has_prov_anaphor_continuation(scan):
                raise OutOfScope("container näistä/niistä provenance continuation")
            if kind in ("section", "container") and _section_tail_carries_kept_content(scan, verb):
                raise OutOfScope("dropped section/container tail keeps old nodes")
            break
        after_sep = scan.peek()
        if after_sep is None or after_sep.cat in ("VERB", "END", "END_SENTINEL_SPAN"):
            # Trailing separator before a new verb group / end: the outer loop
            # owns this separator, so rewind and let the group end.
            scan.goto(saved)
            break
        renumber_tail_nodes = _try_current_section_renumber_tail(scan, verb, chapter, part)
        if renumber_tail_nodes is not None:
            nodes.extend(renumber_tail_nodes)
            last_batch = list(renumber_tail_nodes)
            chapter = _extract_chapter(renumber_tail_nodes, chapter, verb)
            part = _extract_part(renumber_tail_nodes, part)
            continue
        # A VALIOTSIKKO heading backref after a separator (the span includes the
        # separator, matching the old parser). It co-occurs inside a section
        # list and is not itself an insertion/section/container batch.
        val_nodes = _try_valiotsikko(scan, saved)
        if val_nodes is not None:
            nodes.extend(val_nodes)
            last_batch = list(val_nodes)
            continue
        # A mid-list chapter-scoped labelled subheading (``…, D väliotsikko, …``)
        # inheriting the running chapter scope. Tried only when a chapter scope is
        # active; emits a CHAPTER HEADING node and keeps the chapter scope so the
        # following section arms continue to resolve under it.
        csh_nodes = _try_chapter_subheading(scan, chapter, part)
        if csh_nodes is not None:
            nodes.extend(csh_nodes)
            last_batch = list(csh_nodes)
            chapter = _extract_chapter(csh_nodes, chapter, verb)
            part = _extract_part(csh_nodes, part)
            continue
        # A target-first heading-PLACEMENT arm folded into the running list
        # (``<num_list> §:n edelle uusi väliotsikko`` / the ``luvun otsikko``
        # window / ``mukaanluettuna … edellä olevan väliotsikon``). The old
        # ``_target_list`` recognizes these as continuation targets; the scope is
        # the batch chapter/part already threaded.
        #
        # After an INSERTION batch the old parser folds only a SINGLE-section
        # heading placement (``…, 20 §:n edelle uusi väliotsikko``); a placement
        # scoped to MULTIPLE sections (``…, 41 c ja 54 a §:n edelle uusi
        # väliotsikko``) it ends the list on and swallows as residue (2009/1786).
        # Folding the multi-section form here over-produces, so it is rejected:
        # the cursor is rewound and the non-benign tail declines loudly below.
        saved_hp = scan.pos
        hp_nodes = _try_heading_placement(scan, chapter, part)
        if hp_nodes is not None:
            if kind == "insertion" and len(hp_nodes) > 1:
                scan.goto(saved_hp)
            else:
                nodes.extend(hp_nodes)
                last_batch = list(hp_nodes)
                continue
        # A trailing anaphoric heading-placement (``[sen] edellä uusi [N luvun]
        # (väli|ala)otsikko``) after the separator — e.g. ``…uusi 29 a § ja sen
        # edelle uusi väliotsikko``. The old parser consumes this arm but mints NO
        # node for the anaphoric form (verified byte-identical: one SECTION node,
        # whole clause consumed), so swallow it and continue. The non-anaphoric
        # ``N §:n edelle …`` form is owned by _try_heading_placement above (which
        # emits a real node), so this never shadows it — parity preserved.
        if _skip_anaphoric_heading_residue(scan):
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
        # A ``mainitun osan <section|chapter>`` / ``mainitun luvun <section>``
        # resumption arm: after a whole-part / chapter target, these introduce new
        # scoped targets under the resumed part / chapter rather than an anaphor.
        # The old continuation tries them (in this order) under the same BACKREF
        # token after the pure backref-continuation declines. The resumed scope is
        # the threaded batch part / chapter; the inner ref carries its own witness.
        pbr_nodes = _try_part_backref(scan, chapter, part)
        if pbr_nodes is not None:
            nodes.extend(pbr_nodes)
            last_batch = list(pbr_nodes)
            chapter = _extract_chapter(pbr_nodes, chapter, verb)
            part = _extract_part(pbr_nodes, part)
            _consume_inline_move_tails(scan, nodes, last_batch)
            continue
        cbr_nodes = _try_chapter_backref(scan, chapter, part)
        if cbr_nodes is not None:
            nodes.extend(cbr_nodes)
            last_batch = list(cbr_nodes)
            chapter = _extract_chapter(cbr_nodes, chapter, verb)
            part = _extract_part(cbr_nodes, part)
            _consume_inline_move_tails(scan, nodes, last_batch)
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
        # A ``minkä ohella <sec> …`` connective continuation: the old ``_target``
        # reaches past the two-WORD lead-in and keeps the following section ref.
        # Consume the lead-in (only when it leads into a structural section ref)
        # and fall through to ``_recognize_one_target`` at the section, mirroring
        # the old parser. The shape decline guard
        # (``_tail_starts_with_minka_ohella_arm``) is now unreachable for this
        # cue — recognition succeeds instead of declining.
        if kind == "section":
            _skip_minka_ohella_leadin(scan)
            # A ``näistä/niistä <ref> sellaisena kuin … [CITE]`` provenance
            # back-ref re-stating which already-listed target an attribution
            # applies to. The old ``_target_list`` skips it (when closed by a
            # provenance trigger) and re-enters the loop to reach any REAL
            # continuation that follows the closing citation (``…, näistä 5 §:n
            # 4 momentti [CITE], sekä 7 §`` keeps the ``7 §``). Without this skip
            # the section recognizer declines on the anaphor and the following
            # ``sekä 7 §`` is silently dropped as residue.
            #
            # But the same ``näistä …`` tail can instead LEAK a duplicate in the
            # old parser (a ``joista`` arm, an unclosed run, or a SECOND arm after
            # the first closer that the old loop re-parses as a fresh node — e.g.
            # ``näistä 15 a § [CITE], 15 b § sellaisenakuin …``). There the old
            # parser over-claims and the new section path would silently drop the
            # re-parsed node, so the driver DECLINES rather than skip — a clean
            # fail-loud that falls back to the old parser is safe; a silent drop
            # is a corruption. ``_prov_rementtion_leaks`` (faithful to the old
            # skip + its re-parse loop) is exactly that leak/keep oracle: leak →
            # decline; otherwise skip the single closed arm and continue.
            if _prov_rementtion_leaks(scan):
                raise OutOfScope("section näistä/niistä provenance leak")
            if _try_skip_provenance_anaphor_backref(scan):
                continue
        more_batch_start = scan.pos
        try:
            more, more_kind = _recognize_one_target(scan, chapter, part, verb)
        except OutOfScope:
            # An anaphoric-determiner insert arm (``sanottuun pykälään uusi 5
            # momentti`` / ``sanottuun lakiin uusi 4 §`` / ``mainittuun lukuun uusi
            # 7 b §``): the determiner names the target by pointing at a
            # previously-mentioned unit. The old parser routes these FIRST
            # (sp:4212, before the generic statute-name WORD-skip at sp:4680) with a
            # distinct ``fi.anaphoric_determiner_insert`` witness, so they are tried
            # here ahead of ``_retry_target_after_word_skip`` (which self-bails on
            # these determiners). The arm folds into the same verb group; scope
            # carry-forward follows the resolved insert.
            scan.goto(more_batch_start)
            bare_anaphoric_nodes = _try_bare_anaphoric_insert_continuation(
                scan, saved, chapter, verb, nodes
            )
            if bare_anaphoric_nodes is not None:
                nodes.extend(bare_anaphoric_nodes)
                last_batch = list(bare_anaphoric_nodes)
                chapter = _extract_chapter(bare_anaphoric_nodes, chapter, verb)
                part = _extract_part(bare_anaphoric_nodes, part)
                _consume_inline_move_tails(scan, nodes, last_batch)
                continue
            scan.goto(more_batch_start)
            det_nodes = _try_anaphoric_determiner_insert(
                scan, saved, chapter, part, verb, nodes
            )
            if det_nodes is not None:
                det_nodes = _normalize_intrabatch_explicit_part_scope(det_nodes, part)
                nodes.extend(det_nodes)
                last_batch = list(det_nodes)
                chapter = _extract_chapter(det_nodes, chapter, verb)
                part = _extract_part(det_nodes, part)
                _consume_inline_move_tails(scan, nodes, last_batch)
                continue
            scan.goto(more_batch_start)
            # The arm may open with an inline statute-name WORD run the old
            # ``_target_list`` skips before retrying ``_target`` (sp:4679-4697):
            # ``…, työjärjestykseen uusi 52 d §`` / ``…, itse lakiin uusi 63 a §``.
            # The leading document WORD lexes as a bare WORD mid-list (only the
            # FIRST target of a group folds it into a STATUTE_NAME_SPAN), so skip
            # the WORD run and re-attempt the target at the structural noun. The
            # retry's batch witness anchors past the skipped WORDs, matching the
            # old parser (whose witness starts at ``uusi``).
            #
            # The old WORD-skip is the LAST continuation fallback (after the
            # determiner-insert / pykälään-anaphora / heading / backref handlers),
            # so it is only safe when the skipped run leads STRAIGHT into a clean
            # ``uusi <whole-section>`` insert: ``_retry_target_after_word_skip``
            # self-restricts to that shape, declining anything an earlier old
            # handler would have owned.
            retry = _retry_target_after_word_skip(scan, more_batch_start, chapter, part)
            if retry is not None:
                more, more_kind = retry
                nodes.extend(more)
                last_batch = list(more)
                chapter = _chapter_after_batch(scan, more_batch_start, more, chapter, verb)
                part = _extract_part(more, part)
                _consume_inline_move_tails(scan, nodes, last_batch)
                continue
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
            if kind in ("section", "container") and _section_tail_carries_kept_content(scan, verb):
                raise OutOfScope("dropped section/container tail keeps old nodes")
            break
        # An insertion batch followed by a plain section/container continuation
        # arm (``lisätään … uusi 22 a § ja 88 §:n 3 ja 4 momentti``): the old
        # ``_target_list`` folds EVERY continuation arm into the same verb group
        # regardless of node family — under a ``lisätään`` verb a bare ``N §:n M
        # momentti`` arm is recognised by ``_section_ref`` and kept as a plain
        # SECTION node, NOT a SurfaceInsertion. The new section recogniser emits
        # that node identically, so fold it in rather than declining — see the
        # ``_mixed_continuation_is_foldable`` guard below for the cases that are
        # still out of scope (a SCOPED chapter/part section arm that would inherit
        # cross-batch scope this driver threads differently).
        if (kind == "insertion") != (more_kind == "insertion"):
            if not _mixed_continuation_is_foldable(kind, more_kind, more, verb):
                raise OutOfScope("mixed insertion/non-insertion continuation in verb group")
        nodes.extend(more)
        last_batch = list(more)
        chapter = _chapter_after_batch(scan, more_batch_start, more, chapter, verb)
        part = _extract_part(more, part)

        _consume_inline_move_tails(scan, nodes, last_batch)

    # Intra-list part-switch retarget (old sp:4095/4119): when an explicit PART
    # target switches the part mid-list, later bare chapter/section descendants
    # that still carry the pre-switch part are retargeted to the new part (and a
    # section's stale inherited chapter is dropped). The old parser applies this
    # per ``_target`` batch; this driver splits the list at separators differently,
    # so the equivalent fixed point is a single whole-verb-group pass.
    nodes = _normalize_intrabatch_explicit_part_scope(nodes, "")
    if move_dest_part:
        nodes = apply_leading_move_destination_part(nodes, move_dest_part)
    # Advance the discourse context over this group's accumulated nodes (the
    # anchors a following group's anaphoric arm resolves against). Computed as a
    # single whole-group pass — the equivalent fixed point of the per-batch
    # threading, mirroring the part-switch retarget above.
    out_ctx = _update_context_from_nodes(nodes, ctx, verb)
    return verb, nodes, out_ctx


def _mixed_continuation_is_foldable(
    kind: FamilyKind,
    more_kind: FamilyKind,
    more: list[SurfaceNode],
    verb: Optional[SourceVerb],
) -> bool:
    """Whether a family-switching continuation arm may be folded in faithfully.

    The old ``_target_list`` folds EVERY continuation arm into the same verb
    group regardless of node family — its loop just re-runs ``_target`` per
    separator. So an ``lisätään … uusi 22 a § ja 88 §:n 3 ja 4 momentti`` keeps
    BOTH the insertion node and the plain ``88 §:n …`` SECTION node in one LISATA
    group (the second arm is a momentti add recognised by ``_section_ref``, NOT a
    SurfaceInsertion). The reverse (a plain section/replace batch followed by a
    bare ``uusi …`` insertion arm under the SAME verb) folds the same way.

    Fold only when neither side relies on cross-batch scope this driver threads
    differently from the old parser, except for ``lisätään`` section targets that
    are themselves insertion targets. In that family the section recognizer owns
    reinstatement-style insert arms such as ``kumotun 4 §:n 3 momentin tilalle
    uusi 3 momentti``; rejecting the scoped SECTION node makes the parser fall
    back to the legacy path, which truncates later insert arms in 2007/923-style
    clauses.
    """
    if more_kind == "heading":
        # Heading batches already coexist with section/container/insertion lists
        # in the old parser; the caller never routes them here.
        return False
    if kind == "insertion" and more_kind == "section" and verb == SourceVerb.LISATA:
        for node in more:
            if isinstance(node, SurfaceTargetRef):
                if node.kind != TargetKind.SECTION:
                    return False
                if node.part:
                    return False
                if any(sr.facet for sr in node.sub_refs):
                    return False
                continue
            if isinstance(node, SurfaceDescendantCoordination):
                if node.base.kind != TargetKind.SECTION:
                    return False
                if node.base.chapter or node.base.part:
                    return False
                if any(sr.facet for sr in node.arms):
                    return False
                continue
            return False
        return True
    for node in more:
        if isinstance(node, SurfaceInsertion):
            if node.chapter or node.part:
                return False
        elif isinstance(node, SurfaceTargetRef):
            if node.chapter or node.part:
                return False
        elif isinstance(node, SurfaceDescendantCoordination):
            if node.base.chapter or node.base.part:
                return False
        else:
            # A scope block / standalone coordination arm carries cross-batch
            # scope; keep those out of scope for now.
            return False
    return True


def _retry_target_after_word_skip(
    scan: _Scan, arm_start: int, chapter: str, part: str
) -> Optional[tuple[list[SurfaceNode], FamilyKind]]:
    """Skip a leading inline statute-name WORD run, then re-attempt the target.

    Faithful to ``surface_parse._target_list`` sp:4679-4697: a continuation arm
    can open with a document/determiner WORD run the lexer leaves un-annotated
    mid-list (``…, työjärjestykseen uusi 52 d §`` / ``…, itse lakiin uusi 63 a
    §``). The old loop, when ``_target`` declines at the WORD, skips the whole
    WORD run and re-runs ``_target`` at the following structural noun; the batch
    witness then anchors past the skipped WORDs (the old witness starts at
    ``uusi``, NOT at the WORD).

    The cursor is at ``arm_start`` (post-separator). Sentinel spans are skipped
    first (a citation may sit between the separator and the WORD). On no leading
    WORD, or when the re-attempt still declines, the cursor is restored to
    ``arm_start`` and ``None`` is returned so the caller's decline path runs.
    """
    scan.goto(arm_start)
    _skip_sentinels(scan)
    t = scan.peek()
    if t is None or t.cat != "WORD":
        scan.goto(arm_start)
        return None
    # An anaphoric-insert determiner (``sanottuun lakiin uusi …``) is NOT a plain
    # statute-name WORD: the old parser routes it through
    # ``_parse_anaphoric_determiner_insert`` FIRST (sp:4212, before the generic
    # WORD-skip at sp:4680), stamping a distinct ``fi.anaphoric_determiner_insert``
    # witness. Skipping it here would emit the wrong rule_id, so leave it for the
    # dedicated determiner handler / decline path.
    if (t.text or "").lower() in _ANAPHORIC_INSERT_DETERMINERS:
        scan.goto(arm_start)
        return None
    while (t := scan.peek()) is not None and t.cat == "WORD":
        scan.advance()
    # The old WORD-skip fallback sits AFTER every other continuation handler, so
    # it is only safe to fire on the exact shape those handlers do NOT own: a
    # clean ``uusi <whole-section list> §`` insert (``työjärjestykseen uusi 52 d
    # §``). Require the WORD run to lead straight into ``uusi`` and the recognized
    # batch to be pure whole-section insertions (no sub-target, no scope). Any
    # other shape (a bare section ref, a §:ILL/§:GEN sub-target, a chapter/heading
    # arm) is restored and left to decline, mirroring the old precedence.
    t = scan.peek()
    if t is None or t.cat != "UUSI" or t.case != "NOM":
        # Only the nominative ``uusi N §`` whole-section insert is in scope here.
        # The genitive ``uuden N §:n`` past-tense form (``päätökseen uuden 2 a
        # §:n``) is a different shape the old parser folds via its own arm, so
        # leave it to decline rather than recover a divergent grouping.
        scan.goto(arm_start)
        return None
    try:
        more, more_kind = _recognize_one_target(scan, chapter, part)
    except OutOfScope:
        scan.goto(arm_start)
        return None
    if more_kind != "insertion" or not _is_clean_whole_section_insert_batch(more):
        scan.goto(arm_start)
        return None
    return more, more_kind


def _is_clean_whole_section_insert_batch(nodes: list[SurfaceNode]) -> bool:
    """True iff every node is a plain whole-section SurfaceInsertion (no scope)."""
    if not nodes:
        return False
    for node in nodes:
        if not isinstance(node, SurfaceInsertion):
            return False
        if node.kind != TargetKind.SECTION:
            return False
        if node.sub_target is not None or node.chapter or node.part:
            return False
    return True


def _batch_has_doc_ill(scan: _Scan, start: int, end: int) -> bool:
    """True if a DOC:ILL (``asetukseen`` / ``lakiin``) sits in ``[start, end)``.

    Faithful to the old ``_has_doc_ill_in_range``: a batch that re-anchors at the
    statute root via a DOC:ILL resets the inherited chapter scope (old sp:4102),
    so a following bare ``N §:ään uusi …`` arm does NOT inherit a chapter from an
    EARLIER chapter-scoped (``N lukuun uusi …``) insert in the same verb group.
    """
    toks = scan.cur.tokens
    for i in range(start, min(end, len(toks))):
        if toks[i].cat == "DOC" and toks[i].case == "ILL":
            return True
    return False


def _chapter_after_batch(
    scan: _Scan,
    batch_start: int,
    nodes: list[SurfaceNode],
    current: str,
    verb: Optional[SourceVerb],
) -> str:
    """Chapter scope after a batch, with the old DOC:ILL statute-root reset.

    The old ``_target_list`` resets ``chapter = ""`` when the just-parsed batch
    consumed a DOC:ILL re-anchor (sp:4101-4103), BEFORE falling back to the
    node-based extractor. Mirror that so a DOC-reanchored insert (``…, lakiin uusi
    36 a §``) clears the chapter a preceding ``3 lukuun uusi 21 a §`` set.
    """
    if _batch_has_doc_ill(scan, batch_start, scan.pos):
        return ""
    return _extract_chapter(nodes, current, verb)


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
            if all(sr.facet and not sr.momentti for sr in node.sub_refs):
                # Facet-only chapter heading OR a letter-labelled intra-chapter
                # subheading (HEADING facet carrying its letter in ``item``):
                # both scope the following bare section list to this chapter.
                return node.label
            return current
    return extract_chapter(nodes, current)


def _extract_part(nodes: list[SurfaceNode], current: str) -> str:
    """Part scope carried forward from a batch (all wired families)."""
    for node in reversed(nodes):
        if isinstance(node, SurfaceInsertion) and node.part:
            return node.part
    return extract_part(nodes, current)


def _update_context_from_nodes(
    nodes: list[SurfaceNode],
    prior: VerbGroupContext,
    verb: Optional[SourceVerb],
) -> VerbGroupContext:
    """Update the running :class:`VerbGroupContext` from a parsed batch.

    A faithful narrowing of ``surface_parse._extract_section_context_from_nodes``
    to the node types the wired families emit. The running ``chapter`` / ``part``
    advance via the family extractors (``_extract_chapter`` / ``_extract_part``);
    the last-mentioned section anchor (``last_section`` / ``last_section_chapter``
    / ``last_momentti``) is taken from the first SECTION-bearing node walking the
    batch in reverse — a plain section ref, an insertion section, a scope block
    (whose effective chapter is the block's CHAPTER scope label), or a
    descendant-coordination base — each contributing its momentti from the first
    sub-ref / sub-target / coordination arm that carries one. A heading placement
    breaks the walk and contributes only its chapter (no section / momentti).
    When no batch node carries a section, the prior anchor persists unchanged.
    """
    new_chapter = _extract_chapter(nodes, prior.chapter, verb)
    new_part = _extract_part(nodes, prior.part)
    last_section = prior.last_section
    last_section_chapter = prior.last_section_chapter
    last_momentti = prior.last_momentti
    for node in reversed(nodes):
        if isinstance(node, SurfaceHeadingPlacement):
            if node.chapter:
                last_section_chapter = node.chapter
            break
        if isinstance(node, SurfaceScopeBlock) and node.targets:
            last_t = node.targets[-1]
            if not isinstance(last_t, SurfaceTargetRef):
                continue
            if last_t.kind == TargetKind.SECTION and last_t.label:
                last_section = last_t.label
                last_section_chapter = (
                    node.scope_label
                    if node.scope_kind == ScopeKind.CHAPTER
                    else last_t.chapter
                ) or ""
                for sr in last_t.sub_refs:
                    if sr.momentti:
                        last_momentti = sr.momentti
                        break
            break
        if (
            isinstance(node, SurfaceInsertion)
            and node.kind == TargetKind.SECTION
            and node.label
        ):
            last_section = node.label
            last_section_chapter = node.chapter or ""
            if node.sub_target and node.sub_target.momentti:
                last_momentti = node.sub_target.momentti
            break
        if (
            isinstance(node, SurfaceDescendantCoordination)
            and node.base.kind == TargetKind.SECTION
            and node.base.label
        ):
            last_section = node.base.label
            last_section_chapter = node.base.chapter or ""
            for sr in node.arms:
                if sr.momentti:
                    last_momentti = sr.momentti
                    break
            break
        if (
            isinstance(node, SurfaceTargetRef)
            and node.kind == TargetKind.SECTION
            and node.label
        ):
            last_section = node.label
            last_section_chapter = node.chapter or ""
            if node.sub_refs:
                for sr in node.sub_refs:
                    if sr.momentti:
                        last_momentti = sr.momentti
                        break
            break
    return VerbGroupContext(
        last_section=last_section,
        last_section_chapter=last_section_chapter,
        last_momentti=last_momentti,
        chapter=new_chapter,
        part=new_part,
    )


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

    Implemented as a thin projection over :func:`_update_context_from_nodes` from
    an empty prior — the JOLLOIN_MOVE anchor is exactly the batch's last-mentioned
    section (no carry-forward), so only the section / chapter fields are read.
    """
    ctx = _update_context_from_nodes(nodes, VerbGroupContext(), None)
    return ctx.last_section, ctx.last_section_chapter


def _has_later_verb(scan: _Scan) -> bool:
    toks = scan.cur.tokens
    return any(toks[i].cat == "VERB" for i in range(scan.pos, len(toks)))


def _has_operative_target_before_verb(scan: _Scan, start: int) -> bool:
    """True iff a structural target (``§`` / ``luku`` / ``liite``) appears from
    ``start`` before the next VERB / end.

    Used to confirm that real operative targets follow a ``nojalla`` authority
    basis before declining (a bare authority-basis clause with no following target
    is not a silent-drop). Stops at a provenance opener (``sellaisena kuin``) so a
    pure amendment-history span does not count as an operative target.
    """
    toks = scan.cur.tokens
    n = len(toks)
    for i in range(start, n):
        cat = toks[i].cat
        if cat == "VERB":
            return False
        if cat == "WORD" and (toks[i].text or "").lower().startswith("sella"):
            return False
        if cat in ("PYKALA", "LUKU", "LIITE"):
            return True
    return False


# Lead-in token categories naming the operative statute behind a ``nojalla``
# authority basis (``… nojalla [date] annetun asetuksen N §`` / ``mainitun lain
# täytäntöönpanosta … annetun asetuksen N §``). The real targets sit AFTER a bare
# statute name; the skip below crosses only this benign lead-in trivia.
_NOJALLA_LEADIN_CATS = frozenset(
    {"WORD", "NUM", "CITE", "PUNCT", "COMMA", "CONJ", "BACKREF", "DOC", "DASH"}
)


def _num_begins_operative_target(toks: Sequence[Token], i: int) -> bool:
    """True iff the NUM at ``i`` opens an operative ``N (a) (, M …) §`` target list
    (a structural PYKALA closes the number-list run), not a date / citation numeral.

    Scans the contiguous number-list run (``NUM [LETTER] ((COMMA|CONJ) NUM …)*``)
    and requires it to terminate in a PYKALA. A date numeral (``7 päivänä``) or a
    citation year is followed by a WORD / CITE, so it fails this test.
    """
    n = len(toks)
    j = i
    expect_num = True
    while j < n:
        cat = toks[j].cat
        if expect_num:
            if cat != "NUM":
                return False
            j += 1
            if j < n and toks[j].cat == "LETTER":
                j += 1
            expect_num = False
            continue
        if cat in ("COMMA", "CONJ"):
            j += 1
            expect_num = True
            continue
        return cat == "PYKALA"
    return False


def _skip_leading_nojalla_authority(scan: _Scan) -> bool:
    """Skip a leading ``N §:n nojalla`` authority basis to the real target list.

    The cursor MUST be positioned at the ``nojalla`` authority WORD (the section
    recognizer has just mis-read the enabling-statute section ``N §`` as a first
    target). The authority basis names the LEGAL POWER under which the amendment is
    made — never an operative target — and the real targets follow behind a BARE
    statute name with no parenthetical id (``… nojalla [date] annetun
    kansaneläkeasetuksen 80 ja 81 §``). Advance over the ``nojalla`` lead-in (date
    words, ``annetun``, the bare statute-name WORD/DOC, an optional citation) and
    land the cursor at the first operative target token.

    Returns ``True`` and leaves the cursor at the real target start on success;
    ``False`` (cursor unchanged) when the shape is not a clean leading-authority
    skip. Faithful narrowing of the old ``_skip_authority_nojalla_lead_in``
    (surface_parse 2992-3063), restricted to the LEADING bare-name ``§``-list form.
    A second ``nojalla`` (a deeper authority-INSERT shape, e.g. 1993/169 ``1, 4 ja
    7 §:n nojalla uuden 8 §``) bails so that genuinely fuzzy shape stays declined.
    """
    start = scan.pos
    toks = scan.cur.tokens
    n = len(toks)
    if start >= n:
        return False
    cur = toks[start]
    if cur.cat != "WORD" or (cur.text or "").lower() != "nojalla":
        return False
    i = start + 1
    while i < n:
        tok = toks[i]
        cat = tok.cat
        if cat == "VERB":
            return False
        # A provenance opener (``sellaisina kuin``) before any operative target
        # means the authority basis is the only structural content — not our shape.
        if cat == "WORD" and (tok.text or "").lower().startswith("sella"):
            return False
        # A second ``nojalla`` is a deeper authority-insert shape (fuzzy): bail.
        if cat == "WORD" and (tok.text or "").lower() == "nojalla":
            return False
        # An UUSI insertion anchor: a leading-authority insertion arm starts here.
        if cat == "UUSI":
            scan.goto(i)
            return True
        # The first operative section number begins the real target list (a bare
        # NUM-list governed by a PYKALA). Land here and let the families recognize.
        if cat == "NUM" and _num_begins_operative_target(toks, i):
            scan.goto(i)
            return True
        if cat not in _NOJALLA_LEADIN_CATS:
            return False
        i += 1
    return False


# Infinitive amendment-verb WORDs the lexer does NOT lex as a VERB token (archaic
# drafting: ``… muutetaan 1 §:n …, sekä lisätä 4 §:ään uuden 4 momentin``). The
# old parser recognizes the infinitive ``lisätä`` as a second amendment verb and
# keeps its insert list; the new lexer leaves it as a bare WORD, so the outer loop
# would swallow the whole second clause as residue. When such a WORD leads a
# trailing residue that still carries operative insert content (a ``UUSI`` token),
# the dropped tail keeps old nodes — decline loudly so the old parser handles it.
_INFINITIVE_VERB_WORDS = frozenset(
    {
        "lisätä",
        "lisättävä",
        "muuttaa",
        "muutettava",
        "kumota",
        "kumottava",
        "siirtää",
        "siirrettävä",
    }
)


# Prefixes of finite amendment verbs the lexer may leave as a bare WORD when
# misspelled (``muutetaaan`` with four a's instead of ``muutetaan``). A verb-like
# WORD immediately leading a bare structural section is a kept second amendment
# clause the old parser parses; the new lexer dropped it.
_AMEND_VERB_WORD_PREFIXES = ("muuteta", "lisät", "kumot", "siirre", "muutet")


def _residue_carries_infinitive_verb_insert(scan: _Scan) -> bool:
    """True iff a no-further-VERB residue holds an amendment verb the lexer left as
    a bare WORD, leading into kept operative content.

    Two shapes the old parser keeps but the new lexer drops as residue:

      * an infinitive amendment verb (``… sekä lisätä 4 §:ään uuden 4 momentin``)
        followed by a ``UUSI`` insert arm, and
      * a MISSPELLED finite verb (``… sekä muutetaaan 2 §``) the lexer failed to
        recognise, immediately followed by a bare structural section (``§``).

    Requires the verb WORD AND following operative content (``UUSI`` / a ``PYKALA``
    within a short window) so a pure benign WORD run (a misspelled END marker,
    discourse trivia) does not trip it.
    """
    toks = scan.cur.tokens
    n = len(toks)
    for i in range(scan.pos, n):
        if toks[i].cat != "WORD":
            continue
        low = (toks[i].text or "").lower()
        if low in _INFINITIVE_VERB_WORDS:
            if any(toks[k].cat == "UUSI" for k in range(i + 1, n)):
                return True
            continue
        # A misspelled finite amendment verb immediately leading a bare section.
        if any(low.startswith(p) for p in _AMEND_VERB_WORD_PREFIXES):
            for k in range(i + 1, min(i + 4, n)):
                if toks[k].cat == "PYKALA":
                    return True
                if toks[k].cat not in ("NUM", "LETTER", "DASH", "WORD"):
                    break
    return False


def _skip_tilalle_uusi_tail(scan: _Scan) -> bool:
    """Consume a bounded ``tilalle uusi <number-list> <unit>`` insert tail.

    The preceding section-family target already emitted the legal address in
    reinstatement forms like ``4 §:n 3 momentin tilalle uusi 3 momentti``. The
    remaining tail is source evidence for that insertion slot, not a separate
    target. Keep this strictly local: no forward search, and the tail must close
    with an explicit legal unit token.
    """
    saved = scan.pos
    if not ((t := scan.peek()) and t.cat == "TILALLE"):
        return False
    scan.advance()
    if not ((t := scan.peek()) and t.cat == "UUSI"):
        scan.goto(saved)
        return False
    scan.advance()
    saw_number = False
    while (t := scan.peek()) is not None:
        if t.cat == "NUM":
            saw_number = True
            scan.advance()
            if (letter := scan.peek()) is not None and letter.cat == "LETTER":
                scan.advance()
            continue
        if saw_number and t.cat in {"COMMA", "CONJ", "DASH", "SEKA"}:
            scan.advance()
            continue
        break
    if not saw_number:
        scan.goto(saved)
        return False
    if (t := scan.peek()) and t.cat in {"PYKALA", "MOMENTTI", "KOHTA", "LUKU", "OSA"}:
        scan.advance()
        return True
    scan.goto(saved)
    return False


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

    A trailing ``WORD`` run is benign ONLY when it runs straight to end-of-stream
    (no VERB, no structural target after it): a misspelled ``seuraavasti`` END
    marker the lexer left as a WORD, or stray discourse trivia the old outer loop
    swallows to reach the end. A WORD that LEADS INTO a later VERB / structural
    target is NOT benign — it can open a move tail (``johon samalla siirretään
    …``) or anchor (``sanottuun lakiin …``) the old parser folds into the group,
    so the insertion tail must stay out of scope rather than truncate the group.
    """
    toks = scan.cur.tokens
    saw_word = False
    for i in range(scan.pos, len(toks)):
        cat = toks[i].cat
        if cat == "VERB":
            # A WORD run that precedes a later VERB may open a move/anchor tail the
            # old parser folds into THIS group; only a pure benign run (no WORD)
            # up to the verb is safe.
            return not saw_word
        if cat in _BENIGN_TAIL_CATS:
            continue
        if cat == "WORD":
            saw_word = True
            continue
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

    # The first verb group in a same-clause move construction can be preceded by
    # a destination-chapter lead-in (``lakiin uusi 3 a luku, johon samalla
    # siirretään muutettu 11 §``). The main loop skips leading non-verb tokens,
    # so probe that prefix on a throwaway scan first (old sp:5072).
    leading_move_destination_chapter = recognize_leading_move_destination_chapter(
        _Scan(Cursor(tokens))
    )

    scan = _Scan(Cursor(tokens))

    # Skip leading non-verb tokens (the old parser does the same).
    while not scan.cur.at_end and ((t := scan.peek()) is None or t.cat != "VERB"):
        scan.advance()

    if scan.cur.at_end:
        raise OutOfScope("no amendment verb (meta-only clause)")

    verb_groups: list[SurfaceVerbGroup] = []

    # The discourse context threaded across verb groups (the anchors a later
    # group's anaphoric arm resolves against). Initialised empty.
    ctx = VerbGroupContext()

    verb, nodes, ctx = _parse_verb_group(
        scan,
        ctx,
        jolloin_renumber_pairs,
        consumed_jolloin_positions,
        consumed_jolloin_contexts,
    )
    if not nodes:
        # The first verb names a target none of the wired families recognizes
        # (an un-modelled named provision). The old parser drops the group
        # entirely (``if nodes:`` at sp:5107) and the outer loop advances to the
        # next VERB. There must be a later verb whose group carries the operative
        # targets, else the whole clause is empty — out of scope. The verb-seeking
        # skip below (mirroring sp:5137-5148) then reaches that next group.
        if not _has_later_verb(scan):
            raise OutOfScope("empty first verb group")
    else:
        if leading_move_destination_chapter and verb == SourceVerb.SIIRTAA:
            # The leading-destination move group's sections take the inserted
            # chapter as their move destination (old sp:5109-5131).
            nodes = apply_leading_move_destination_chapter(
                nodes, leading_move_destination_chapter
            )
        verb_groups.append(
            SurfaceVerbGroup(
                verb=VerbKind.from_code(verb or SourceVerb.MUUTTAA), nodes=tuple(nodes)
            )
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
                # outer loop swallows by advancing to end. But an infinitive
                # amendment verb (``… sekä lisätä 4 §:ään uuden 4 momentin``) the
                # lexer left as a bare WORD carries a kept insert list the old
                # parser keeps — silently swallowing it would drop those inserts,
                # so decline loudly instead.
                if _residue_carries_infinitive_verb_insert(scan):
                    raise OutOfScope(
                        "infinitive amendment-verb residue keeps insert list"
                    )
                # Consume the benign residue so consumed_count matches, then stop.
                while not scan.cur.at_end:
                    scan.advance()
                break
        verb2, nodes2, ctx = _parse_verb_group(
            scan,
            ctx,
            jolloin_renumber_pairs,
            consumed_jolloin_positions,
            consumed_jolloin_contexts,
        )
        if not nodes2:
            # An empty subsequent group: the verb named an un-modelled target
            # (the family recognizers declined its first target). The old parser
            # (sp:5154-5158) keeps scanning for further verb groups when the
            # group consumed any tokens past the loop-iteration start, and only
            # stops (rewinding the separator) when nothing advanced.
            if scan.pos > saved:
                continue
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
