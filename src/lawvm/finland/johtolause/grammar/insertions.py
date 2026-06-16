"""insertions — the insertion recognizer family (rewrite slice 2).

The second real recognizer family of the combinator-based replacement for
``surface_parse.py``. It recognizes the ``lisätään … uusi …`` shapes a Finnish
amendment verb group lists after its verb — clauses that ADD new structure:

    [N osan] [N luvun] DOC:ILL              uusi numlist (§ | luku | osa)
    [N osan] [N luvun] N LUKU:ILL           uusi numlist (§ | luku)
    [N osan]           N OSA:ILL            uusi numlist (§ | luku)
                       DOC:ILL N LUKU:ILL   uusi numlist §        (prefix-chapter)
                       numlist §:ILL        uusi sub_target       (momentti / kohta)
                       numlist §:GEN        uusi sub_target
                       numlist §:GEN  M MOMENTTI:ILL/GEN  uusi sub_target
                                           uusi numlist (§ | luku | osa)   (cite-stripped)

and emits the frozen ``SurfaceInsertion`` nodes byte-identically to the old
``_insertion`` / ``_insertion_sub_target``.

Two enforced layers (per the rewrite contract):

  * LOUD recognizers — pure functions over a ``_Scan`` that return a structured
    intermediate (``ParsedInsertion``) carrying only what was seen (kind, labels,
    sub-targets, scope). No frozen-node construction, no witnesses.
  * a thin emitter (``emit_insertion_nodes``) that turns the intermediate into
    the frozen ``SurfaceInsertion`` nodes (label number+suffix normalization,
    range expansion). The driver stamps the shared batch witness (the old
    parser's ``_stamp_default_witness`` semantics: one span per batch).

Witness ``rule_id``s emitted here (the closed set for this slice, inferred from
node shape exactly as ``_stamp_default_witness`` does):
``fi.insertion_section``, ``fi.insertion_sub_target``, ``fi.insertion_chapter``,
``fi.insertion_heading`` (only when an ``uusi otsikko`` heading is co-emitted).

The archaic ``näin kuuluva`` / ``näin kuluva`` lead-in between the insertion
anchor and the structural target is skipped (faithful to the old parser's
``_skip_archaic_nain_kuuluva`` at every arm), so a ``lisätään lakiin uusi näin
kuuluva N §`` is the same clean insertion as ``lisätään lakiin uusi N §``.

A reinstatement preamble between a ``N lukuun`` chapter anchor and ``uusi``
(``N lukuun siitä lailla X kumotun K §:n tilalle uusi M §``) is consumed by the
chapter-scoped arm itself (faithful to old Pattern F); a CITATION_SPAN there is a
chapter provenance attribution the old parser scopes differently, so it declines.
The plain genitive whole-section variant (``uuden N §:n`` with no momentti/kohta)
is reproduced — the old parser consumes the §:GEN and emits a whole-section
insert.

Out of scope for slice 2 (the recognizer returns None / the driver raises
``OutOfScope`` for these): heading-placement inserts (``§:n edelle uusi
väliotsikko``), the §:ILL / §:GEN / DOC:ILL reinstatement preamble forms
(``kumotun N §:n tilalle uusi`` between a §/DOC anchor and ``uusi``), the
enumeration-truncation continuation arms, postfix-chapter ``§ lukuun N`` lists,
appendix inserts, malformed ``§ luku`` chapter inserts, backref/anaphora,
move/renumber tails, jolloin, and meta/text-amend. Whenever a clause needs any of
those, the recognizer declines so the driver treats the clause as out of scope
rather than miscompiling it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from lawvm.core.semantic_types import FacetKind
from lawvm.finland.johtolause.grammar.combinators import Span
from lawvm.finland.johtolause.grammar.sections import (
    NumSuffix,
    _Scan,
    _chapter_ctx,
    _expand_range_single,
    _letter,
    _number_list,
    _part_ctx,
    _sep,
)
from lawvm.finland.source_verb import SourceVerb
from lawvm.finland.johtolause.surface_model import (
    SurfaceInsertion,
    SurfaceNode,
    SurfaceSubRef,
    TargetKind,
)

class OutOfScopeInsertion(Exception):
    """Raised by the recogniser for a phrase that IS an insertion but is out of
    scope for this slice (so the driver must decline the whole clause rather than
    fall through to the section-reference reading and mis-compile the insertion's
    authority / reinstated-slot list as operative section targets).
    """


# ---------------------------------------------------------------------------
# The recognized insertion (the intermediate the emitter consumes).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InsSubTarget:
    """A parsed insertion sub-target: a momentti or kohta being inserted."""

    momentti: int = 0
    item: str = ""
    facet: Optional[FacetKind] = None


@dataclass(frozen=True, slots=True)
class ParsedInsertion:
    """A recognized insertion batch: spans + per-node payload.

    Architecture-neutral: each emitted node is described by ``(kind, label,
    chapter, part, sub_target)`` exactly as the old parser populated the
    ``SurfaceInsertion`` fields, but the frozen node + witness is built by the
    emitter / driver. ``nums`` is kept implicit — the recognizer pre-expands
    ranges into the per-node payloads so the emitter is a pure 1:1 map.
    """

    span: Span
    nodes: tuple["InsNode", ...]


@dataclass(frozen=True, slots=True)
class InsNode:
    """One inserted entity (pre-emit form)."""

    kind: TargetKind
    label: str
    chapter: str = ""
    part: str = ""
    sub_target: Optional[InsSubTarget] = None


# ---------------------------------------------------------------------------
# Out-of-scope sentinels.
#
# The clean slice-2 subset excludes any clause that requires reinstatement /
# citation / provenance handling, archaic markers, headings, appendices, or the
# continuation/postfix tails. We detect these and decline (the recognizer
# returns None, the driver raises OutOfScope) rather than risk a silent
# miscompile against the old parser's elaborate residue handling.
# ---------------------------------------------------------------------------

# Categories whose presence inside an insertion phrase signals an out-of-scope
# shape (reinstatement / tilalle / citation / provenance / heading / archaic).
_OOS_CATS = frozenset(
    {
        "REINST_SPAN",
        "CITATION_SPAN",
        "PROVENANCE_SPAN",
        "STATUTE_NAME_SPAN",
        "PROV",
        "TILALLE",
        "EDELLA",
        "OTSIKKO",
        "LIITE",
        "BACKREF",
        "VALIOTSIKKO",
    }
)

# OOS markers that are PROVENANCE attributions (reinstatement / citation spans).
# These only ever attach to the arm they close — a downstream batch's provenance
# never belongs to the current arm — so the guard scans for them only inside the
# CURRENT insertion arm. (A genuine reinstatement preamble between this arm's
# anchor and its ``uusi`` still sits inside the arm boundary and is caught.)
_OOS_PROVENANCE_CATS = frozenset(
    {"REINST_SPAN", "CITATION_SPAN", "PROVENANCE_SPAN", "STATUTE_NAME_SPAN", "PROV", "TILALLE"}
)
# OOS markers that signal an in-batch HEADING / APPENDIX / BACKREF fold the old
# parser performs across the WHOLE remaining phrase (it folds a trailing
# ``§:n edelle … väliotsikko`` / ``liite`` / anaphoric backref arm into the same
# ``_target`` batch span). These must stay scanned to the next VERB / END so a
# clause whose later arm is such a fold still declines rather than mis-grouping.
_OOS_STRUCTURAL_CATS = frozenset({"EDELLA", "OTSIKKO", "LIITE", "BACKREF", "VALIOTSIKKO"})


# ---------------------------------------------------------------------------
# Leaf reads.
# ---------------------------------------------------------------------------


def _at(scan: _Scan, *cats: str) -> bool:
    t = scan.peek()
    return t is not None and t.cat in cats


def _at_cat_case(scan: _Scan, cat: str, case: str) -> bool:
    t = scan.peek()
    return t is not None and t.cat == cat and t.case == case


def _at_cat_cases(scan: _Scan, cat: str, *cases: str) -> bool:
    t = scan.peek()
    return t is not None and t.cat == cat and t.case in cases


def _consume_uusi(scan: _Scan) -> bool:
    if _at(scan, "UUSI"):
        scan.advance()
        return True
    return False


def _optional_comma(scan: _Scan) -> None:
    if _at(scan, "COMMA"):
        scan.advance()


# Archaic ``näin kuuluva`` / ``näin kuluva`` insert lead-ins (including the glued
# ``näinkuuluva`` / ``näinkuluva`` spelling) sit between the insertion anchor and
# the structural target in historical statutes. The old parser skips them at
# every insertion arm (``_skip_archaic_nain_kuuluva``); we reproduce that skip so
# a ``lisätään lakiin uusi näin kuuluva N §`` is the same clean insertion as
# ``lisätään lakiin uusi N §``.
_NAIN_KUULUVA_TAIL = frozenset({"kuuluva", "kuulua", "kuluva"})
_NAIN_KUULUVA_GLUED = frozenset({"näinkuuluva", "näinkuluva"})


def _skip_nain_kuuluva(scan: _Scan) -> None:
    """Skip an archaic ``näin kuuluva`` lead-in (faithful to the old parser)."""
    t0 = scan.peek()
    if t0 is None:
        return
    t0_lemma = (t0.lemma or "").lower()
    t0_text = (t0.text or "").lower()
    if t0_lemma == "näin":
        t1 = scan.peek(1)
        if t1 is not None and (t1.lemma or "").lower() in _NAIN_KUULUVA_TAIL:
            scan.advance(2)
            return
    if t0_lemma in _NAIN_KUULUVA_GLUED or t0_text in _NAIN_KUULUVA_GLUED:
        scan.advance()


# Reinstatement / citation / provenance sentinel spans skipped between a §:ILL
# target and ``uusi`` (the old parser's ``_TILALLE_OR_REINST | {PROVENANCE_SPAN}``;
# the ``§:ILL`` arm DOES skip a bare ``tilalle`` token here).
_ILL_REINST_SPANS = frozenset({"TILALLE", "REINST_SPAN", "CITATION_SPAN", "PROVENANCE_SPAN"})
# Reinstatement / citation closers that terminate the §:GEN ``_skip_to_uusi``
# scan (the old parser's ``_TILALLE_OR_REINST``).
_TILALLE_OR_REINST = frozenset({"TILALLE", "REINST_SPAN", "CITATION_SPAN"})
# Span-only reinstatement run skipped after the §:GEN momentti (the old parser's
# ``_REINST_OR_CITE | {PROVENANCE_SPAN}`` — notably this does NOT include a bare
# ``tilalle`` token, so ``N §:n M momentin tilalle uusi M momentti`` is left to
# the section-ref reading, exactly as the old parser does).
_GEN_REINST_SPANS = frozenset({"REINST_SPAN", "CITATION_SPAN", "PROVENANCE_SPAN"})


def _skip_ill_reinst_preamble(scan: _Scan) -> None:
    """Skip the §:ILL reinstatement preamble (old Pattern A, lines 2390-2421).

    After the ``N §:ILL`` target: an optional comma; a run of
    tilalle / reinstatement / citation / provenance spans; an optional comma; a
    ``N momentin/kohdan tilalle`` numeric-keyed clause; and a ``b kohdan
    tilalle`` letter-keyed clause. Each explicit clause is taken ONLY when closed
    by a tilalle / reinstatement token, otherwise rewound (it is a genuine
    sub-target left for ``uusi``'s sub-target path).
    """
    _optional_comma(scan)
    while _at(scan, *_ILL_REINST_SPANS):
        scan.advance()
    _optional_comma(scan)

    # ``N momentin/kohdan tilalle`` — numeric-keyed reinstatement preamble.
    saved = scan.pos
    nums = _number_list(scan)
    if nums and _at(scan, "MOMENTTI", "KOHTA"):
        scan.advance()
        if _at(scan, *_TILALLE_OR_REINST):
            scan.advance()
        else:
            scan.goto(saved)
    elif nums:
        scan.goto(saved)

    # ``b kohdan tilalle`` — letter-keyed reinstatement preamble.
    saved = scan.pos
    if _at(scan, "LETTER"):
        scan.advance()
        if _at(scan, "KOHTA"):
            scan.advance()
            if _at(scan, *_TILALLE_OR_REINST):
                scan.advance()
            else:
                scan.goto(saved)
        else:
            scan.goto(saved)


def _skip_gen_reinst_preamble(scan: _Scan) -> None:
    """Skip the §:GEN momentti reinstatement preamble (old Pattern B2, 2502-2519).

    After the ``M MOMENTTI:ILL/GEN`` host: an optional comma; a run of
    reinstatement / citation / provenance SPANS (a bare ``tilalle`` token is NOT
    skipped here); then, only if the next token is a NUM/LETTER, a forward scan
    to the first ``uusi`` that skips through to just after a closing
    tilalle / reinstatement token — and rewinds if no such closer precedes the
    next ``uusi`` (or a VERB intervenes).
    """
    _optional_comma(scan)
    while _at(scan, *_GEN_REINST_SPANS):
        scan.advance()

    saved = scan.pos
    if not _at(scan, "NUM", "LETTER"):
        return
    toks = scan.cur.tokens
    i = scan.pos
    while i < len(toks) and toks[i].cat != "UUSI":
        if toks[i].cat in _TILALLE_OR_REINST:
            scan.goto(i + 1)
            return
        if toks[i].cat == "VERB":
            break
        i += 1
    scan.goto(saved)


# Reinstatement / citation / provenance spans skipped between a DOC:ILL anchor
# (``lakiin`` / ``asetukseen``) and ``uusi`` (the old Pattern C's
# ``_REINST_OR_CITE | {PROVENANCE_SPAN}`` — notably this does NOT include a bare
# ``tilalle`` token, so a residual ``tilalle`` after the run leaves ``uusi``
# unreachable and the arm declines, exactly as the old parser does).
_DOC_REINST_SPANS = frozenset({"REINST_SPAN", "CITATION_SPAN", "PROVENANCE_SPAN"})


def _skip_doc_reinst_preamble(scan: _Scan) -> None:
    """Skip the DOC:ILL reinstatement preamble (old Pattern C, line 2651).

    A run of reinstatement / citation / provenance SPANS between the
    ``lakiin`` / ``asetukseen`` anchor and ``uusi``. A bare ``TILALLE`` token is
    deliberately NOT skipped (the old skip-set excludes it).
    """
    while _at(scan, *_DOC_REINST_SPANS):
        scan.advance()


# ---------------------------------------------------------------------------
# Sub-target recognition (faithful narrowing of _insertion_sub_target).
#
# Only the clean momentti / kohta arms are recognized. The heading arm
# (``uusi otsikko``) and the letter-only / archaic arms are out of scope.
# ---------------------------------------------------------------------------


def _recognize_sub_target(scan: _Scan, sec: str, chapter: str, part: str, mom_ctx: int) -> Optional[list[InsNode]]:
    """After ``uusi``, recognize a momentti/kohta sub-target. None if not clean.

    Faithful to ``_insertion_sub_target`` restricted to the in-scope arms:

      * ``uusi N momentin M kohta``  (genitive momentti container + kohta)
      * ``uusi N momentti``          (nominative momentti insert)
      * ``uusi N kohta``             (kohta insert, defaulting momentti to ctx or 1)

    Heading (``uusi otsikko``) and any reading that needs reinstatement is
    declined (returns None). An archaic ``näin kuuluva`` lead-in between ``uusi``
    and the sub-target is skipped (faithful to the old parser, line 1891).
    """
    _skip_nain_kuuluva(scan)

    if _at(scan, "OTSIKKO"):
        # Heading insertion is out of scope for this slice.
        return None

    nums = _number_list(scan)
    if not nums:
        # Letter-only item: ``uusi b kohta`` (old _insertion_sub_target 1931-1944).
        saved_let = scan.pos
        let = _letter(scan)
        if let is not None and _at(scan, "KOHTA"):
            scan.advance()
            eff_mom = mom_ctx or 1
            return [
                InsNode(
                    kind=TargetKind.SECTION,
                    label=sec,
                    chapter=chapter,
                    part=part,
                    sub_target=InsSubTarget(momentti=eff_mom, item=let),
                )
            ]
        scan.goto(saved_let)
        return None

    # ``uusi N momentin M kohta`` — genitive momentti is a container qualifier
    # for a kohta insertion into the existing momentti N.
    if _at_cat_case(scan, "MOMENTTI", "GEN") and nums[0][0].isdigit():
        saved = scan.pos
        scan.advance()
        kohta_nums = _number_list(scan)
        if kohta_nums and _at(scan, "KOHTA"):
            scan.advance()
            mom = int(nums[0][0])
            out: list[InsNode] = []
            for kn, ksf in kohta_nums:
                for rk in _expand_range_single(kn):
                    out.append(
                        InsNode(
                            kind=TargetKind.SECTION,
                            label=sec,
                            chapter=chapter,
                            part=part,
                            sub_target=InsSubTarget(momentti=mom, item=rk + ksf),
                        )
                    )
            return out
        scan.goto(saved)

    if _at(scan, "MOMENTTI"):
        scan.advance()
        out = []
        for n, _sf in nums:
            for rn in _expand_range_single(n):
                out.append(
                    InsNode(
                        kind=TargetKind.SECTION,
                        label=sec,
                        chapter=chapter,
                        part=part,
                        sub_target=InsSubTarget(momentti=int(rn) if rn.isdigit() else 0),
                    )
                )
        return out

    if _at(scan, "KOHTA"):
        scan.advance()
        eff_mom = mom_ctx or 1
        out = []
        for n, sf in nums:
            for rn in _expand_range_single(n):
                out.append(
                    InsNode(
                        kind=TargetKind.SECTION,
                        label=sec,
                        chapter=chapter,
                        part=part,
                        sub_target=InsSubTarget(momentti=eff_mom, item=rn + sf),
                    )
                )
        return out

    # ``uusi N §`` / ``uusi N luku`` — a whole-section / whole-chapter insert that
    # the old ``_insertion_sub_target`` (lines 2008-2029) also recognizes at a
    # sub-target position. This is the continuation arm a chained insertion folds
    # into the same §:ILL/§:GEN batch (``130 §:ään uusi 2 ja 3 momentti sekä uusi
    # 145 a §``): the ``145 a §`` is a fresh whole-section insert (the host
    # ``sec`` is not its scope), kept in the batch by the shared witness span.
    if _at(scan, "PYKALA"):
        scan.advance()
        return [
            InsNode(kind=TargetKind.SECTION, label=n + sf, chapter=chapter, part=part)
            for n, sf in nums
        ]
    if _at(scan, "LUKU"):
        scan.advance()
        return [
            InsNode(kind=TargetKind.CHAPTER, label=n + sf, part=part)
            for n, sf in nums
        ]

    return None


# A placeholder label/chapter the bare-anaphoric recognizer stamps on its
# InsNodes; the driver overwrites it with the resolved anaphoric section /
# chapter (the recognizer is pure and never sees the prior-batch context).
_ANAPHORIC_PLACEHOLDER = ""


@dataclass(frozen=True, slots=True)
class ParsedAnaphoricSubTarget:
    """A bare anaphoric ``[§:ILL|§:GEN M momenttiin|momenttiin] uusi <sub>`` arm.

    The recognizer (``recognize_bare_anaphoric_sub_target``) is PURE: it consumes
    the structural-noun anaphor + reinstatement skip + ``uusi`` + the inserted
    momentti/kohta sub-target, and emits ``InsNode``s carrying the parsed
    momentti/item but PLACEHOLDER section/chapter. The driver fills the resolved
    anaphoric ``(section, chapter)`` (and, for the ``momenttiin`` reading, the
    context momentti) from its :class:`VerbGroupContext`.

    ``momentti_from_context`` is True for the bare ``[mainittuun] momenttiin uusi
    K kohta`` reading: the kohta's host momentti is the LAST-mentioned momentti,
    which only the driver knows — it stamps it onto every emitted node. For the
    ``§:ään`` and ``§:n M momenttiin`` readings the momentti is explicit in the
    text (or defaulted to 1 by ``_recognize_sub_target``), so the flag is False.
    """

    span: Span
    nodes: tuple[InsNode, ...]
    momentti_from_context: bool


def recognize_bare_anaphoric_sub_target(
    scan: _Scan,
) -> Optional[ParsedAnaphoricSubTarget]:
    """Recognize ``[§:ILL | §:GEN M momenttiin | momenttiin] uusi <sub>`` at cursor.

    The anaphoric determiner (``sanottuun`` / ``mainittuun`` / …) has ALREADY been
    consumed by the driver; the cursor sits on the structural-noun anaphor. A pure
    narrowing of the PYKALA / MOMENTTI branches of the old
    ``_parse_anaphoric_determiner_insert`` (surface_parse 3965-4009):

      * ``§:ILL [reinst] uusi <sub>``      — section anaphora (``sanottuun
        pykälään uusi 5 momentti``); host = prior section, momentti from the text.
      * ``§:GEN M MOMENTTI:ILL uusi <sub>`` — section-genitive then momentti
        (``saman pykälän 2 momenttiin uusi 4 kohta``); the explicit ``M`` is the
        kohta's host momentti.
      * ``MOMENTTI:ILL uusi <sub>``         — momentti anaphora (``mainittuun
        momenttiin uusi 5 kohta``); the kohta's host momentti is the prior
        momentti (``momentti_from_context``).

    Emits ``InsNode``s with placeholder section/chapter for the driver to fill.
    Returns None (cursor restored) for any other shape. The recognizer stays PURE:
    it never reads the prior-batch context, so it cannot itself decide the bare
    ``momenttiin`` reading is valid (the driver declines if no prior momentti).
    """
    saved = scan.pos
    t = scan.peek()
    if t is None:
        return None

    # ``§:ään [reinst/prov] uusi <sub>`` — section anaphora.
    if t.cat == "PYKALA" and t.case == "ILL":
        scan.advance()  # consume §:ILL
        _optional_comma(scan)
        while _at(scan, *_ILL_REINST_SPANS):
            scan.advance()
        _optional_comma(scan)
        if not _consume_uusi(scan):
            scan.goto(saved)
            return None
        nodes = _recognize_sub_target(
            scan, _ANAPHORIC_PLACEHOLDER, _ANAPHORIC_PLACEHOLDER, "", 0
        )
        if nodes:
            return ParsedAnaphoricSubTarget(
                span=Span(saved, scan.pos),
                nodes=tuple(nodes),
                momentti_from_context=False,
            )
        scan.goto(saved)
        return None

    # ``§:n M momenttiin uusi <sub>`` — section-genitive then explicit momentti.
    if t.cat == "PYKALA" and t.case == "GEN":
        scan.advance()  # consume §:GEN
        mom_nums = _number_list(scan)
        if mom_nums and _at_cat_case(scan, "MOMENTTI", "ILL"):
            mom_num = int(mom_nums[0][0]) if mom_nums[0][0].isdigit() else 0
            scan.advance()  # consume MOMENTTI:ILL
            _optional_comma(scan)
            if _consume_uusi(scan):
                nodes = _recognize_sub_target(
                    scan, _ANAPHORIC_PLACEHOLDER, _ANAPHORIC_PLACEHOLDER, "", mom_num
                )
                if nodes:
                    return ParsedAnaphoricSubTarget(
                        span=Span(saved, scan.pos),
                        nodes=tuple(nodes),
                        momentti_from_context=False,
                    )
        scan.goto(saved)
        return None

    # ``momenttiin uusi <sub>`` — momentti anaphora (host momentti from context).
    if t.cat == "MOMENTTI" and t.case == "ILL":
        scan.advance()  # consume MOMENTTI:ILL
        _optional_comma(scan)
        if _consume_uusi(scan):
            # The host momentti is supplied by the driver; recognize the kohta
            # sub-target with a zero placeholder, which the driver overwrites.
            nodes = _recognize_sub_target(
                scan, _ANAPHORIC_PLACEHOLDER, _ANAPHORIC_PLACEHOLDER, "", 0
            )
            if nodes:
                return ParsedAnaphoricSubTarget(
                    span=Span(saved, scan.pos),
                    nodes=tuple(nodes),
                    momentti_from_context=True,
                )
        scan.goto(saved)
        return None

    return None


def recognize_bare_anaphoric_chapter_insert(
    scan: _Scan, verb: Optional[SourceVerb]
) -> Optional[list[InsNode]]:
    """Recognize a bare ``lukuun [reinst] uusi numlist (§ | luku)`` chapter insert.

    The anaphoric determiner (``mainittuun`` / ``samaan`` / …) has ALREADY been
    consumed by the driver; the cursor sits on a NUMBER-less ``LUKU:ILL`` — the
    ``said chapter``, whose number is the inherited running chapter. A pure
    narrowing of the old Pattern E (surface_parse 2841-2877): an optional
    reinstatement span and a ``<nums> §:GEN tilalle`` partial-reinstatement clause
    (taken only when tilalle-closed), then ``uusi numlist (§ | luku)`` — or, under
    ``LISATA``, the same number-list with the ``uusi`` left implicit.

    The emitted ``InsNode``s carry a PLACEHOLDER chapter the driver fills with the
    inherited chapter. Returns None (cursor restored) for any other shape.
    """
    saved = scan.pos
    if not _at_cat_case(scan, "LUKU", "ILL"):
        return None
    scan.advance()  # consume bare LUKU:ILL
    if _at(scan, *_TILALLE_OR_REINST):
        scan.advance()

    # Residual ``<nums> §:GEN tilalle`` partial-reinstatement clause.
    saved_rf = scan.pos
    rf_nums = _number_list(scan)
    if rf_nums and _at_cat_case(scan, "PYKALA", "GEN"):
        scan.advance()
        if _at(scan, *_TILALLE_OR_REINST):
            scan.advance()
        else:
            scan.goto(saved_rf)
    elif rf_nums:
        scan.goto(saved_rf)

    if _consume_uusi(scan):
        nums2 = _number_list(scan)
        if nums2 and _at(scan, "PYKALA"):
            scan.advance()
            return [
                InsNode(kind=TargetKind.SECTION, label=n + sf, chapter=_ANAPHORIC_PLACEHOLDER)
                for n, sf in nums2
            ]
        scan.goto(saved)
        return None

    # No-``uusi`` LISATA fallback: ``lukuun numlist (§ | luku)`` (uusi implicit).
    if verb == SourceVerb.LISATA:
        scan.goto(saved)
        scan.advance()  # re-consume bare LUKU:ILL
        nums2 = _number_list(scan)
        t = scan.peek()
        if nums2 and t is not None and t.cat in ("PYKALA", "LUKU") and t.case != "GEN":
            kind = TargetKind.SECTION if t.cat == "PYKALA" else TargetKind.CHAPTER
            scan.advance()
            return [
                InsNode(kind=kind, label=n + sf, chapter=_ANAPHORIC_PLACEHOLDER)
                for n, sf in nums2
            ]
    scan.goto(saved)
    return None


# ---------------------------------------------------------------------------
# Whole-target insertion list: ``uusi numlist (§ | luku | osa)``.
# ---------------------------------------------------------------------------


def _recognize_whole_target_list(
    scan: _Scan, chapter: str, part: str
) -> Optional[list[InsNode]]:
    """Recognize ``numlist (§ | luku | osa)`` after ``uusi`` (whole-target inserts).

    Faithful to the old Pattern D structural-noun dispatch (lines 2910-2960):

      * ``numlist OSA``                    → whole-part inserts.
      * ``numlist §:GEN M momentti/kohta`` → a sub-target insert into ``§``.
      * ``numlist §:GEN`` (no momentti/kohta) → the genitive whole-section
        *stylistic* variant (``uuden N §:n``); the old parser consumes the §:GEN
        and emits a plain whole-section insert, so it is reproduced here.
      * ``numlist §`` / ``numlist luku`` (nominative/illative) → plain
        whole-section / whole-chapter inserts.

    Declines (returns None) only for the malformed ``§ luku`` chapter-repair form.
    """
    nums = _number_list(scan)
    if not nums:
        return None
    t = scan.peek()
    if t is None:
        return None
    if t.cat == "OSA":
        scan.advance()
        return [InsNode(kind=TargetKind.PART, label=n + sf, chapter=chapter, part=part) for n, sf in nums]
    if t.cat not in ("PYKALA", "LUKU"):
        return None

    # ``numlist §:GEN M momentti/kohta`` — a sub-target insert into the single
    # section (old Pattern D, lines 2916-2947). Only the §:GEN form carries this.
    if t.cat == "PYKALA" and t.case == "GEN":
        saved_gen = scan.pos
        scan.advance()  # consume §:GEN
        sub_nums = _number_list(scan)
        st = scan.peek()
        if sub_nums and st is not None and st.cat in ("MOMENTTI", "KOHTA"):
            is_kohta = st.cat == "KOHTA"
            scan.advance()
            sec_num = nums[0][0] + nums[0][1]
            out: list[InsNode] = []
            for n, sf in sub_nums:
                for rn in _expand_range_single(n):
                    if is_kohta:
                        st_sub = InsSubTarget(momentti=1, item=rn + sf)
                    else:
                        st_sub = InsSubTarget(momentti=int(rn) if rn.isdigit() else 0)
                    out.append(
                        InsNode(
                            kind=TargetKind.SECTION,
                            label=sec_num,
                            chapter=chapter,
                            part=part,
                            sub_target=st_sub,
                        )
                    )
            return out
        # Plain genitive whole-section stylistic variant (``uuden N §:n`` with no
        # momentti/kohta). The old parser restores to the §:GEN (saved_gen here),
        # falls through to the whole-section emit below (kind=SECTION, consume the
        # §, one node per number) — the §:GEN is NOT a malformed ``§ luku`` form
        # (a NOM LUKU never directly follows a GEN §:n), so the chapter-repair
        # guard cannot fire. Rewind and let the shared whole-section tail own it.
        scan.goto(saved_gen)

    # Malformed ``§ luku`` (PYKALA immediately followed by a NOM LUKU) is an
    # old-parser chapter-repair path — out of scope here.
    if t.cat == "PYKALA":
        t1 = scan.peek(1)
        if t1 is not None and t1.cat == "LUKU" and t1.case == "NOM":
            return None
    kind = TargetKind.SECTION if t.cat == "PYKALA" else TargetKind.CHAPTER
    scan.advance()
    return [InsNode(kind=kind, label=n + sf, chapter=chapter, part=part) for n, sf in nums]


# ---------------------------------------------------------------------------
# Top-level insertion recognizer.
# ---------------------------------------------------------------------------


def recognize_insertion(
    scan: _Scan,
    chapter: str = "",
    part: str = "",
    verb: Optional[SourceVerb] = None,
) -> Optional[ParsedInsertion]:
    """Recognize a clean insertion batch at the cursor.

    Mirrors the in-scope arms of ``surface_parse._insertion`` (Patterns A, A-1,
    A0/G, B2, B3, F, C, D), declining every out-of-scope feature. Returns a
    ``ParsedInsertion`` (the intermediate) or None (recoverable: the caller
    restores and treats the clause as out of scope).

    ``chapter`` / ``part`` are the intra-group inherited scope (empty at the
    first batch). The old parser threads ``effective_chapter = ch_pre or
    chapter`` so a later bare ``N §:ään uusi …`` arm inherits the preceding
    ``N luvun`` scope; this is reproduced here.

    ``verb`` is the verb-group verb (``None`` when unknown). Only ``LISATA``
    enables the no-``uusi`` DOC:ILL / LUKU:ILL fallback arms the old parser
    accepts (``lisätään … lakiin N §`` with the ``uusi`` left implicit).
    """
    start = scan.pos

    # Citation-stamped authority lead-in (``N §:n nojalla uusi …``): the leading
    # GEN authority section is NOT an operative target; skip it so the insertion
    # anchors at ``uusi``. Probed before container context so the authority's own
    # chapter/part is not mistaken for the inserted entity's scope.
    saved_auth = scan.pos
    if _skip_authority_nojalla_lead_in(scan) and _at(scan, "UUSI"):
        scan.advance()
        _skip_nain_kuuluva(scan)
        post_uusi = scan.pos
        auth_nodes = _recognize_whole_target_list(scan, chapter, part)
        if auth_nodes:
            return ParsedInsertion(span=Span(start, scan.pos), nodes=tuple(auth_nodes))
        # The ``nojalla`` authority lead-in fired but ``uusi`` is followed by a
        # bare number list with NO structural noun (``… nojalla uusi 8 b`` — the
        # old parser's historical bare-section insert). This is an out-of-scope
        # authority insertion the recogniser cannot reproduce; raise so the
        # driver declines rather than mis-reading the authority §:GEN list as a
        # plain section reference. A number list CLOSED by a structural noun
        # (``… uusi 2 momentti`` / ``… uusi 9 kohta``) is a genuine sub-target
        # insert instead — rewind and let the normal dispatch own it.
        scan.goto(post_uusi)
        bare_nums = _number_list(scan)
        bare_next = scan.peek()
        scan.goto(saved_auth)
        if bare_nums and (
            bare_next is None
            or bare_next.cat not in ("MOMENTTI", "KOHTA", "PYKALA", "LUKU", "OSA")
        ):
            raise OutOfScopeInsertion(
                "out-of-scope authority insertion (bare-number insert after nojalla lead-in)"
            )
    else:
        scan.goto(saved_auth)

    # Pre-parse optional container context (``N osan`` / ``N luvun``), exactly as
    # ``_insertion`` does before the pattern dispatch.
    part_pre = _part_ctx(scan) or ""
    ch_pre = _chapter_ctx(scan) or ""
    effective_part = part_pre or part
    effective_chapter = ch_pre or chapter

    # Any reinstatement/citation/provenance/heading token inside the phrase means
    # the clause needs the old parser's residue handling — out of scope.
    nodes = _dispatch(scan, effective_part, effective_chapter, verb)
    if nodes is None:
        scan.goto(start)
        return None
    if not nodes:
        scan.goto(start)
        return None
    return ParsedInsertion(span=Span(start, scan.pos), nodes=tuple(nodes))


def _phrase_has_oos_token(scan: _Scan, end: int, cats: frozenset[str] = _OOS_CATS) -> bool:
    """True if any token in [scan.pos, end) is an out-of-scope marker in ``cats``."""
    toks = scan.cur.tokens
    for i in range(scan.pos, min(end, len(toks))):
        if toks[i].cat in cats:
            return True
    return False


def _next_verb_or_end(scan: _Scan) -> int:
    """Index of the next VERB / END token at/after the cursor (or len)."""
    toks = scan.cur.tokens
    for i in range(scan.pos, len(toks)):
        if toks[i].cat in ("VERB", "END", "END_SENTINEL_SPAN"):
            return i
    return len(toks)


# Structural nouns that close an inserted-target arm (the inserted entity itself).
_ARM_CLOSE_NOUNS = frozenset({"PYKALA", "LUKU", "OSA", "MOMENTTI", "KOHTA", "LIITE"})


def _whole_target_arm_boundary(scan: _Scan, hard: int) -> int:
    """Boundary for the whole-target OOS guard: the end of THIS insertion arm.

    The phrase-level out-of-scope guard must inspect only the CURRENT insertion
    arm, not a downstream continuation batch joined by a separator. A reinstatement
    / citation / heading / appendix marker that forces this arm out of scope always
    sits in the arm's own span — before its ``uusi`` (a residue preamble) or as the
    inserted entity right after it — never past the separator that ends the arm.

    Faithful arm extent: walk to the first ``uusi``, then past its inserted-target
    payload (the ``<nums> (§|luku|osa|momentti|kohta|liite)`` it closes on, plus any
    chained ``sep uusi <nums>`` pre-noun groups), and return the position of the
    first list separator (``,`` / ``ja`` / ``sekä``) after that closing noun — the
    point where a fresh continuation batch begins. Falls back to ``hard`` (the next
    VERB / END) when no such arm-closing structure is found, so a malformed arm with
    no ``uusi`` keeps the old whole-phrase scan (never under-declines).
    """
    toks = scan.cur.tokens
    i = scan.pos
    # Find the first ``uusi`` opening this arm (before any VERB / END).
    while i < hard and toks[i].cat != "UUSI":
        i += 1
    if i >= hard:
        return hard
    i += 1  # past ``uusi``
    # Walk the inserted-target payload to its FIRST closing structural noun, then
    # past any further chained ``sep [uusi] <nums> (noun)`` groups in the same arm
    # (``uusi 5 ja 6 sekä uusi 7 §``). The boundary is the first separator that does
    # NOT lead back into another ``<nums> noun`` chain group — i.e. the next batch.
    seen_close = False
    while i < hard:
        cat = toks[i].cat
        if cat in _ARM_CLOSE_NOUNS:
            seen_close = True
            i += 1
            continue
        if cat in ("COMMA", "CONJ", "SEKA"):
            if not seen_close:
                # A separator before any closing noun is internal to the arm's
                # ``uusi <nums> ja <nums> …`` number list — keep walking.
                i += 1
                continue
            # A separator after a closing noun ends this arm.
            return i
        i += 1
    return hard


# Structural-authority token categories that may precede a citation-stamped
# ``nojalla`` authority lead-in (``N §:n M momentin nojalla uusi …``).
_AUTHORITY_CATS = frozenset(
    {"NUM", "LETTER", "DASH", "CONJ", "PYKALA", "MOMENTTI", "LUKU", "OSA"}
)


def _skip_authority_nojalla_lead_in(scan: _Scan) -> bool:
    """Skip a ``<GEN authority> [CITE] uusi`` lead-in to the ``uusi``.

    A faithful narrowing of ``_target._skip_authority_nojalla_lead_in`` for the
    citation-stamped form: when the operative phrase opens with structural
    authority tokens ending in a genitive (``N §:n [M momentin]``) followed
    IMMEDIATELY by a CITATION_SPAN (the ``nojalla``/``sellaisena kuin se on``
    provenance) and then ``uusi``, the leading section is the AUTHORITY for the
    insertion, not an operative target. Advances the cursor to the ``uusi`` and
    returns True; otherwise leaves the cursor untouched and returns False.

    The CITATION_SPAN must sit DIRECTLY before ``uusi`` (the provenance closes
    the authority phrase): an ``N §:n M momenttiin uusi …`` (illative insertion
    target, no citation before ``uusi``) is a genuine sub-target insert, not an
    authority lead-in, and must NOT be skipped.
    """
    toks = scan.cur.tokens
    i = scan.pos
    n = len(toks)
    saw_authority_gen = False
    while i < n:
        cat = toks[i].cat
        if cat in ("VERB", "END", "END_SENTINEL_SPAN", "UUSI"):
            return False
        if cat == "CITATION_SPAN":
            # The citation must close the authority (GEN seen) and directly
            # precede ``uusi``.
            if saw_authority_gen and (i + 1) < n and toks[i + 1].cat == "UUSI":
                scan.goto(i + 1)
                return True
            i += 1
            continue
        if cat in _AUTHORITY_CATS:
            if cat in ("PYKALA", "MOMENTTI", "LUKU", "OSA") and toks[i].case == "GEN":
                saw_authority_gen = True
            elif cat in ("PYKALA", "MOMENTTI", "LUKU", "OSA"):
                # A non-genitive structural noun (illative insertion target,
                # nominative) means this is not an authority lead-in.
                return False
        elif cat in ("STATUTE_NAME_SPAN", "COMMA"):
            pass
        else:
            return False
        i += 1
    return False


def _dispatch(
    scan: _Scan,
    effective_part: str,
    effective_chapter: str,
    verb: Optional[SourceVerb] = None,
) -> Optional[list[InsNode]]:
    """Try each in-scope insertion arm in the old parser's priority order.

    ``effective_part`` / ``effective_chapter`` are the resolved container scope
    (``ch_pre or inherited``), already including any inherited verb-group scope.
    ``verb`` gates the no-``uusi`` DOC:ILL / LUKU:ILL fallback arms (LISATA only).
    """
    # ── Target-prefixed sub-target arms: numlist (§:ILL | §:GEN) … ──────────
    #
    # Tried FIRST, before the phrase-level out-of-scope guard. The old parser's
    # Pattern A (``N §:ILL [reinst] uusi sub_target``) and Pattern B2 (``N §:GEN
    # M MOMENTTI:ILL [reinst] uusi sub_target``) consume only up to the inserted
    # momentti/kohta and leave any TRAILING provenance / citation span for the
    # outer loop to swallow — so a clean kohta-into-momentti insert with a
    # trailing ``sellaisena kuin se on …`` provenance is still a single
    # ``SurfaceInsertion``, not a section reference. These arms also internally
    # reproduce the reinstatement preamble skip (``[, REINST] [N kohdan tilalle]
    # uusi``) the old parser performs between the §/momentti target and ``uusi``.
    # Each arm self-validates (it accepts only a real momentti/kohta sub-target),
    # so authority / whole-target / out-of-scope shapes still fall through to the
    # broad guard and the section-ref recogniser below.
    pre_nums_pos = scan.pos
    nums = _number_list(scan)
    if nums:
        t = scan.peek()
        if t is not None and t.cat == "PYKALA" and t.case == "ILL":
            sub = _try_section_ill_sub_target(scan, nums, effective_chapter, effective_part)
            if sub is not None:
                return sub
            return None  # §:ILL committed to a sub-target arm; clean form only
        if t is not None and t.cat == "PYKALA" and t.case == "GEN":
            gen = _try_section_gen_sub_target(scan, nums, effective_chapter, effective_part)
            if gen is not None:
                return gen
            return None
        if t is not None and t.cat == "LUKU" and t.case == "ILL":
            # LUKU:ILL chapter-scoped whole-target insert (old Pattern F). Tried
            # here, BEFORE the broad provenance guard, because the old parser
            # skips a reinstatement preamble between ``N lukuun`` and ``uusi``
            # (``N lukuun siitä lailla X kumotun K §:n tilalle uusi M §``). That
            # REINST / TILALLE span is a preamble this arm consumes itself, not an
            # out-of-scope feature — letting the guard see it would wrongly
            # decline. The arm self-validates (it returns None for any non-clean
            # ``uusi numlist (§|luku)`` shape), so out-of-scope LUKU:ILL forms
            # (heading placement / appendix folds) still decline below.
            luku = _try_luku_scoped(scan, nums, effective_part, verb)
            if luku is not None:
                return luku
            scan.goto(pre_nums_pos)
        else:
            scan.goto(pre_nums_pos)

    # ── DOC:ILL arms ───────────────────────────────────────────────────────
    #
    # Dispatched BEFORE the broad provenance guard (like the LUKU:ILL arm), because
    # the old Pattern C skips a reinstatement preamble between the ``lakiin`` /
    # ``asetukseen`` anchor and ``uusi`` (``lakiin siitä lailla X kumotun K §:n
    # tilalle uusi M §`` — the whole reinstatement clause collapses to a single
    # REINST_SPAN / CITATION_SPAN / PROVENANCE_SPAN run). That preamble is consumed
    # by this arm itself, not an out-of-scope feature, so letting the guard see it
    # would wrongly decline. The arm self-validates (it returns None for any
    # non-clean shape, including the heading / appendix / postfix folds), so
    # out-of-scope DOC:ILL forms still fall through to the guard + section-ref path.
    if _at_cat_case(scan, "DOC", "ILL"):
        doc = _try_doc_ill(scan, effective_part, verb)
        if doc is not None:
            return doc
        # A DOC:ILL anchor that the arm could not reproduce as a clean insertion
        # may still carry a downstream heading / appendix fold the old parser folds
        # into the batch — fall through to the broad guard so it declines loudly
        # rather than silently. The arm rewound the cursor on its None return.

    # ── DOC:ILL appendix insert: ``DOC:ILL uusi liite [numlist]`` ──────────
    #
    # Tried before the structural out-of-scope guard (which lists LIITE) so a
    # clean appendix insert is recognized rather than declined. The arm only
    # fires on the exact ``DOC:ILL [,] uusi liite [numlist]`` shape — anything
    # carrying a reinstatement / citation / provenance / heading-anchor before
    # the ``uusi liite`` falls through and the LIITE guard below still declines
    # it (faithful: those forms need the old parser's residue handling).
    if _at_cat_case(scan, "DOC", "ILL"):
        liite = _try_doc_ill_liite(scan)
        if liite is not None:
            return liite

    # Out-of-scope guard for the remaining (whole-target) arms. Two scans:
    #
    #   * Provenance markers (reinstatement / citation / tilalle spans) attach to
    #     the arm they close, so a downstream continuation batch's provenance is
    #     not this arm's concern — scan for them only inside the CURRENT arm.
    #   * Heading / appendix / backref markers signal an in-batch fold the old
    #     parser performs across the whole remaining phrase (folding a trailing
    #     ``§:n edelle … väliotsikko`` / ``liite`` arm into the same batch span);
    #     this driver cannot reproduce that grouping — scan to the next VERB / END.
    hard = _next_verb_or_end(scan)
    arm_boundary = _whole_target_arm_boundary(scan, hard)
    if _phrase_has_oos_token(scan, arm_boundary, _OOS_PROVENANCE_CATS):
        return None
    if _phrase_has_oos_token(scan, hard, _OOS_STRUCTURAL_CATS):
        return None

    # ── OSA:ILL-scoped insert: ``[N] OSA:ILL uusi numlist (§ | luku)`` ──────
    osa_nodes = _try_osa_scoped(scan, effective_part)
    if osa_nodes is not None:
        return osa_nodes

    # (The LUKU:ILL-scoped insert arm is dispatched above, before the broad
    # provenance guard, so its reinstatement preamble is consumed faithfully.)

    # ── Bare ``uusi numlist (§ | luku | osa)`` (citation-stripped) ──────────
    if _at(scan, "UUSI"):
        scan.advance()
        _skip_nain_kuuluva(scan)
        whole = _recognize_whole_target_list(scan, effective_chapter, effective_part)
        if whole is not None:
            return whole
        return None

    return None


# ---------------------------------------------------------------------------
# Per-arm recognizers (each faithful to one clean branch of _insertion).
# ---------------------------------------------------------------------------


def _try_osa_scoped(scan: _Scan, effective_part: str) -> Optional[list[InsNode]]:
    """``[N] OSA:ILL [,] uusi numlist (§ | luku)`` — part-scoped insertion.

    Covers the clean ``V osaan uusi 2 ja 3 luku`` form (explicit part numeral)
    and the inherited-part continuation ``osaan uusi 4-13 luku``.
    """
    saved = scan.pos

    explicit_part = ""
    pn = _number_list(scan)
    if pn and len(pn) == 1 and _at_cat_case(scan, "OSA", "ILL"):
        explicit_part = pn[0][0] + pn[0][1]
        scan.advance()
    else:
        scan.goto(saved)
        if _at_cat_case(scan, "OSA", "ILL") and effective_part:
            scan.advance()
        else:
            scan.goto(saved)
            return None

    _optional_comma(scan)
    if not _consume_uusi(scan):
        scan.goto(saved)
        return None
    _skip_nain_kuuluva(scan)
    ins_nums = _number_list(scan)
    t = scan.peek()
    if ins_nums and t is not None and t.cat in ("PYKALA", "LUKU") and t.case != "GEN":
        kind = TargetKind.SECTION if t.cat == "PYKALA" else TargetKind.CHAPTER
        scan.advance()
        part_label = explicit_part or effective_part
        return [InsNode(kind=kind, label=n + sf, chapter="", part=part_label) for n, sf in ins_nums]
    scan.goto(saved)
    return None


def _consume_sub_target_continuation(
    scan: _Scan, sec_nums: list[str], chapter: str, part: str, all_nodes: list[InsNode]
) -> None:
    """Absorb anaphoric ``sep [uusi] <bare sub-target>`` chains (Pattern A loop).

    Faithful to ``_insertion``'s while-loop (lines 2442-2483): the same
    section(s) gain further momentti/kohta sub-targets joined by ``sekä/ja``,
    where the repeated ``uusi`` is optional. A continuation without ``uusi`` is
    only taken when it is a genuine bare sub-target (NUM/LETTER lead) and every
    parsed node is a sub-target insertion of the same section(s) — otherwise the
    cursor is rewound so a fresh ``N §`` / ``N §:ään`` target is left for the
    driver's separator loop.
    """
    while True:
        saved_c = scan.pos
        if scan.peek() is None or not _at(scan, "COMMA", "CONJ"):
            scan.goto(saved_c)
            break
        # Consume the separator (comma / conjunction cluster).
        scan.advance()
        while _at(scan, "COMMA", "CONJ"):
            scan.advance()
        had_uusi = _consume_uusi(scan)
        if not had_uusi:
            nxt = scan.peek()
            if nxt is None or nxt.cat not in ("NUM", "LETTER"):
                scan.goto(saved_c)
                break
        batch: list[InsNode] = []
        for sec in sec_nums:
            saved_sub = scan.pos
            more = _recognize_sub_target(scan, sec, chapter, part, 0)
            if more:
                batch.extend(more)
            if sec != sec_nums[-1]:
                scan.goto(saved_sub)
        if not batch:
            scan.goto(saved_c)
            break
        if not had_uusi and not all(
            n.sub_target is not None
            and (n.sub_target.momentti or n.sub_target.item or n.sub_target.facet)
            for n in batch
        ):
            scan.goto(saved_c)
            break
        all_nodes.extend(batch)


def _try_section_ill_sub_target(
    scan: _Scan, nums: list[NumSuffix], chapter: str, part: str
) -> Optional[list[InsNode]]:
    """``numlist §:ILL [,] uusi sub_target`` — momentti/kohta insertion.

    The §:ILL has already been peeked (not consumed). Consumes it, the old
    parser's reinstatement preamble (``[, REINST] [N kohdan tilalle] uusi``),
    then requires ``uusi``. Absorbs the anaphoric ``ja/sekä <bare sub-target>``
    chain that the old parser keeps in the same batch.
    """
    saved = scan.pos
    scan.advance()  # consume §:ILL
    _skip_ill_reinst_preamble(scan)
    # An archaic ``näin kuuluva`` lead-in can sit between the §:ään target (and
    # its skipped provenance) and ``uusi`` (old _insertion line 2421).
    _skip_nain_kuuluva(scan)
    if not _consume_uusi(scan):
        scan.goto(saved)
        return None
    sec_nums = [n + sf for n, sf in nums]
    all_nodes: list[InsNode] = []
    for sec in sec_nums:
        saved_sub = scan.pos
        sub = _recognize_sub_target(scan, sec, chapter, part, 0)
        if sub:
            all_nodes.extend(sub)
        if sec != sec_nums[-1]:
            scan.goto(saved_sub)
    if not all_nodes:
        scan.goto(saved)
        return None
    _consume_sub_target_continuation(scan, sec_nums, chapter, part, all_nodes)
    return all_nodes


def _try_section_gen_sub_target(
    scan: _Scan, nums: list[NumSuffix], chapter: str, part: str
) -> Optional[list[InsNode]]:
    """``numlist §:GEN [M MOMENTTI:ILL/GEN] uusi sub_target`` (Patterns B2/B3)."""
    saved = scan.pos
    scan.advance()  # consume §:GEN
    m_nums = _number_list(scan)
    if m_nums and _at_cat_cases(scan, "MOMENTTI", "ILL", "GEN"):
        scan.advance()
        m_num = int(m_nums[0][0]) if m_nums[0][0].isdigit() else 0
        _skip_gen_reinst_preamble(scan)
        if not _consume_uusi(scan):
            scan.goto(saved)
            return None
        sec_nums = [n + sf for n, sf in nums]
        all_nodes: list[InsNode] = []
        for sec in sec_nums:
            saved_sub = scan.pos
            sub = _recognize_sub_target(scan, sec, chapter, part, m_num)
            if sub:
                all_nodes.extend(sub)
            if sec != sec_nums[-1]:
                scan.goto(saved_sub)
        # Unlike the §:ILL Pattern A arm, the old §:GEN Patterns B2/B3
        # (surface_parse lines 2520-2548) have NO anaphoric continuation loop: a
        # following ``ja uusi N §`` / ``sekä uusi M momentti`` arm starts a fresh
        # ``_target`` batch (its own witness span), so it is NOT folded here —
        # the driver's separator loop owns it.
        if all_nodes:
            return all_nodes
        scan.goto(saved)
        return None

    # Pattern B3: ``numlist §:GEN uusi sub_target`` (no intervening momentti).
    if not m_nums and _consume_uusi(scan):
        sec_nums = [n + sf for n, sf in nums]
        all_nodes = []
        for sec in sec_nums:
            saved_sub = scan.pos
            sub = _recognize_sub_target(scan, sec, chapter, part, 0)
            if sub:
                all_nodes.extend(sub)
            if sec != sec_nums[-1]:
                scan.goto(saved_sub)
        if all_nodes:
            return all_nodes
    scan.goto(saved)
    return None


def _try_luku_scoped(
    scan: _Scan,
    nums: list[NumSuffix],
    effective_part: str,
    verb: Optional[SourceVerb] = None,
) -> Optional[list[InsNode]]:
    """``N LUKU:ILL [reinst preamble] uusi numlist (§ | luku)`` — chapter-scoped insert.

    Faithful to the old Pattern F (lines 2550-2610). Between ``N lukuun`` and
    ``uusi`` the old parser skips a reinstatement preamble:

      * an optional comma;
      * a single TILALLE / REINST_SPAN span (``siitä lailla X kumotun K §:n
        tilalle`` collapses to one token). A CITATION_SPAN here is a chapter
        provenance attribution the old parser scopes differently (chapter=''), so
        the arm declines rather than mis-scoping the inserted section;
      * a residual ``<nums> §:GEN tilalle`` partial-reinstatement clause (taken
        only when closed by a tilalle/reinst token, else rewound).

    A heading-placement fold (``<nums> §:GEN edelle uusi otsikko``) the old parser
    would consume and emit in-batch is NOT reproducible here, so its presence
    (an EDELLA / OTSIKKO before the inserted ``uusi``) declines the arm.
    """
    saved = scan.pos
    scan.advance()  # consume LUKU:ILL
    chap_num = nums[0][0] + nums[0][1]
    _optional_comma(scan)

    # A CITATION_SPAN between ``lukuun`` and ``uusi`` is a provenance attribution
    # of the chapter (``N lukuun, sellaisena kuin se on laissa X, uusi M §``). The
    # old parser's chapter-scope pre-parse swallows the ``N lukuun , [CITE]`` run
    # and ``_insertion`` then declines (returning the section via the bare-target
    # path with chapter=''). Reproduce that by declining the chapter-scoped arm
    # here, so the citation case is NOT mis-scoped to the chapter.
    if _at(scan, "CITATION_SPAN"):
        scan.goto(saved)
        return None

    # Single tilalle / reinstatement span (the collapsed reinstatement preamble:
    # ``siitä lailla X kumotun K §:n tilalle``). CITATION_SPAN is excluded above.
    if _at(scan, "TILALLE", "REINST_SPAN"):
        scan.advance()

    # Residual ``<nums> §:GEN tilalle`` partial-reinstatement clause.
    saved_rf = scan.pos
    rf_nums = _number_list(scan)
    if rf_nums and _at_cat_case(scan, "PYKALA", "GEN"):
        scan.advance()
        if _at(scan, "TILALLE", "REINST_SPAN"):
            scan.advance()
        else:
            scan.goto(saved_rf)
    elif rf_nums:
        scan.goto(saved_rf)

    # A heading-placement fold here is out of scope (cannot reproduce in-batch).
    if _at(scan, "EDELLA", "OTSIKKO"):
        scan.goto(saved)
        return None

    if not _consume_uusi(scan):
        # No-``uusi`` fallback (old Pattern F, surface_parse line 2600): under a
        # ``lisätään`` verb the ``uusi`` may be implicit, so ``N lukuun numlist
        # (§ | luku)`` is still a chapter-scoped insert. Faithful to the old
        # ``elif`` arm: the cursor is NOT restored (it continues from after the
        # comma / reinstatement skips), and the closing structural noun must be
        # non-genitive.
        if verb == SourceVerb.LISATA:
            ins_nums = _number_list(scan)
            t = scan.peek()
            if (
                ins_nums
                and t is not None
                and t.cat in ("PYKALA", "LUKU")
                and t.case != "GEN"
            ):
                kind = TargetKind.SECTION if t.cat == "PYKALA" else TargetKind.CHAPTER
                scan.advance()
                return [
                    InsNode(kind=kind, label=n + sf, chapter=chap_num, part=effective_part)
                    for n, sf in ins_nums
                ]
        scan.goto(saved)
        return None
    _skip_nain_kuuluva(scan)
    ins_nums = _number_list(scan)
    t = scan.peek()
    if ins_nums and t is not None and t.cat in ("PYKALA", "LUKU"):
        kind = TargetKind.SECTION if t.cat == "PYKALA" else TargetKind.CHAPTER
        scan.advance()
        return [
            InsNode(kind=kind, label=n + sf, chapter=chap_num, part=effective_part)
            for n, sf in ins_nums
        ]
    scan.goto(saved)
    return None


# Heading-facet / postfix-chapter markers whose presence in a post-``§`` DOC
# continuation arm means the old parser folds an arm this recogniser cannot
# reproduce in-batch (heading placement / postfix chapter). Their presence
# forces the whole DOC insertion to decline.
_DOC_CONT_OOS_CATS = frozenset({"EDELLA", "OTSIKKO", "VALIOTSIKKO", "LIITE"})


def _fold_doc_section_continuation(scan: _Scan, out_nodes: list[InsNode]) -> bool:
    """Fold post-``§`` ``[sekä/ja] [DOC:ILL] [uusi] <nums> § (NOM)`` arms.

    Faithful to the in-scope subset of the old Pattern C continuation loop
    (surface_parse lines 2726-2764): a plain whole-section continuation joined by
    a separator extends the SAME batch. Each folded arm appends SECTION
    ``InsNode``s to ``out_nodes`` so they share the driver's batch witness span.

    Returns ``True`` when the continuation was fully folded / cleanly ended (the
    cursor is left after the last folded arm, with any unparsed separator rewound
    so the driver's loop owns the next arm). Returns ``False`` when a continuation
    arm the old parser would fold into this batch is one this recogniser cannot
    reproduce (a heading-placement or postfix-chapter arm: ``<nums> § lukuun`` /
    ``<nums> §:n edelle … väliotsikko``) — the caller then declines the whole
    insertion rather than emit a divergent grouping / span.
    """
    while True:
        cont_saved = scan.pos
        if _sep(scan) is None:
            scan.goto(cont_saved)
            return True
        # Optional ``…, lakiin uusi …`` re-anchor and repeated ``uusi``.
        if _at_cat_case(scan, "DOC", "ILL"):
            scan.advance()
        _consume_uusi(scan)

        arm_start = scan.pos
        more_nums = _number_list(scan)
        nxt = scan.peek()
        if more_nums and nxt is not None and nxt.cat == "PYKALA" and nxt.case == "NOM":
            # A ``<nums> § lukuun <chap>`` postfix-chapter arm is folded by the old
            # parser into this batch but is out of scope here — decline.
            after = scan.peek(1)
            if after is not None and after.cat == "LUKU" and after.case == "ILL":
                scan.goto(cont_saved)
                return False
            scan.advance()  # consume § (NOM)
            out_nodes.extend(
                InsNode(kind=TargetKind.SECTION, label=n + sf, chapter="")
                for n, sf in more_nums
            )
            continue

        # Not a plain NOM-section arm. If this arm carries a heading-facet /
        # postfix / appendix marker (which the old parser folds into THIS batch),
        # we cannot reproduce it — decline. Otherwise (a §:ILL / §:GEN sub-target
        # arm, or any other shape the old parser leaves for the outer target-list
        # loop) rewind to the separator and let the driver own the next arm.
        boundary = _doc_cont_arm_boundary(scan, arm_start)
        if _arm_has_oos_marker(scan, arm_start, boundary):
            scan.goto(cont_saved)
            return False
        scan.goto(cont_saved)
        return True


def _doc_cont_arm_boundary(scan: _Scan, start: int) -> int:
    """Index of the next separator / verb / end at/after ``start`` (one arm)."""
    toks = scan.cur.tokens
    for i in range(start, len(toks)):
        if toks[i].cat in ("VERB", "END", "END_SENTINEL_SPAN", "COMMA", "CONJ", "SEKA"):
            return i
    return len(toks)


def _arm_has_oos_marker(scan: _Scan, start: int, end: int) -> bool:
    """True if a heading / postfix / appendix marker lives in [start, end)."""
    toks = scan.cur.tokens
    for i in range(start, min(end, len(toks))):
        if toks[i].cat in _DOC_CONT_OOS_CATS:
            return True
        # A ``§ lukuun`` postfix-chapter arm: a PYKALA immediately followed by a
        # LUKU:ILL destination.
        if (
            toks[i].cat == "PYKALA"
            and i + 1 < len(toks)
            and toks[i + 1].cat == "LUKU"
            and toks[i + 1].case == "ILL"
        ):
            return True
    return False


def _try_doc_ill_liite(scan: _Scan) -> Optional[list[InsNode]]:
    """``DOC:ILL [,] uusi liite [numlist]`` — appendix (liite) insertion.

    Faithful to the old appendix arm (surface_parse lines 2664-2670): after the
    DOC:ILL anchor and ``uusi``, a ``liite`` closes a whole-appendix insert.
    A trailing number list gives one APPENDIX node per appendix number; with no
    number the appendix is unlabelled (``label=""``). All appendix inserts return
    to statute level, so ``chapter`` / ``part`` are empty.

    Only the clean shape fires: a comma is the sole element tolerated between the
    DOC:ILL anchor and ``uusi`` (mirroring the old optional comma). Any
    reinstatement / citation / provenance / named-heading-anchor between the
    anchor and ``uusi liite`` leaves the cursor rewound (returns None) so the
    broad LIITE out-of-scope guard still declines the clause — those forms need
    the old parser's residue handling this recogniser does not reproduce.
    """
    saved = scan.pos
    scan.advance()  # consume DOC:ILL
    _optional_comma(scan)
    if not _consume_uusi(scan):
        scan.goto(saved)
        return None
    _skip_nain_kuuluva(scan)
    if not _at(scan, "LIITE"):
        scan.goto(saved)
        return None
    scan.advance()  # consume LIITE
    post_nums = _number_list(scan)
    if post_nums:
        return [
            InsNode(kind=TargetKind.APPENDIX, label=n + sf, chapter="", part="")
            for n, sf in post_nums
        ]
    return [InsNode(kind=TargetKind.APPENDIX, label="", chapter="", part="")]


def _try_doc_ill_no_uusi(
    scan: _Scan, saved: int, verb: Optional[SourceVerb]
) -> Optional[list[InsNode]]:
    """No-``uusi`` DOC:ILL insert fallback (old Pattern C, surface_parse 2828).

    ``lisätään … lakiin N §`` leaves the ``uusi`` implicit. Faithful to the old
    fallback: only under ``LISATA``; restore to the DOC:ILL anchor and re-consume
    it fresh (NO comma / reinstatement skip), then accept a clean
    ``numlist (§ | luku)`` whose closing structural noun is non-genitive. The
    inserted entity is statute-level (chapter=''). Any other shape returns None
    (the caller restores and declines).
    """
    if verb != SourceVerb.LISATA:
        return None
    scan.goto(saved)
    scan.advance()  # re-consume DOC:ILL
    nums2 = _number_list(scan)
    if not nums2:
        return None
    t = scan.peek()
    if t is None or t.cat not in ("PYKALA", "LUKU") or t.case == "GEN":
        return None
    kind = TargetKind.SECTION if t.cat == "PYKALA" else TargetKind.CHAPTER
    scan.advance()
    return [InsNode(kind=kind, label=n + sf, chapter="") for n, sf in nums2]


def _try_doc_ill(
    scan: _Scan, part: str, verb: Optional[SourceVerb] = None
) -> Optional[list[InsNode]]:
    """``DOC:ILL [N LUKU:ILL] uusi numlist (§ | luku | osa)`` — doc-level insert.

    Covers the dominant ``lakiin uusi N §`` form, the whole-part/whole-chapter
    variants, and the prefix-chapter ``DOC:ILL N lukuun uusi M §``. Declines the
    GEN §/luku sub-target variant, the appendix/heading/postfix/continuation
    tails, and any chained ``sekä/ja uusi …`` enumeration (out of scope).

    When ``verb`` is ``LISATA`` and no clean ``uusi``-anchored shape matches, a
    final no-``uusi`` fallback (old parser line 2828) accepts ``DOC:ILL numlist
    (§ | luku)`` with the ``uusi`` left implicit by ``lisätään`` — e.g. the
    ``ja lakiin 19 a §`` second batch of ``lisätään 16 §:ään uusi … ja lakiin
    19 a §``. The inserted entity returns to statute level (chapter='').
    """
    saved = scan.pos
    scan.advance()  # consume DOC:ILL
    _optional_comma(scan)

    # Reinstatement preamble between the ``lakiin`` / ``asetukseen`` anchor and
    # ``uusi`` (old Pattern C, surface_parse line 2651:
    # ``s.skip_cats(_REINST_OR_CITE | {PROVENANCE_SPAN})``). The whole
    # ``siitä lailla X kumotun K §:n tilalle`` reinstatement clause collapses to a
    # single REINST_SPAN / CITATION_SPAN / PROVENANCE_SPAN run; the inserted
    # section's scope is the statute (chapter='') — the consumed reinstatement
    # slot does NOT propagate onto the inserted node. A bare ``TILALLE`` token is
    # NOT skipped here (the old parser's skip-set excludes it), so a residual
    # ``tilalle`` left after the span run leaves ``uusi`` unreachable and the arm
    # declines, exactly as the old parser does.
    _skip_doc_reinst_preamble(scan)

    # Prefix-chapter: ``DOC:ILL N lukuun [,] uusi M §``.
    saved_pc = scan.pos
    pc_nums = _number_list(scan)
    if pc_nums and len(pc_nums) == 1 and _at_cat_case(scan, "LUKU", "ILL"):
        pc_chapter = pc_nums[0][0] + pc_nums[0][1]
        scan.advance()
        _optional_comma(scan)
        if _consume_uusi(scan):
            _skip_nain_kuuluva(scan)
            pc2 = _number_list(scan)
            t = scan.peek()
            if pc2 and t is not None and t.cat == "PYKALA" and t.case != "GEN":
                scan.advance()
                return [
                    InsNode(kind=TargetKind.SECTION, label=n + sf, chapter=pc_chapter)
                    for n, sf in pc2
                ]
        scan.goto(saved_pc)
    else:
        scan.goto(saved_pc)

    if not _consume_uusi(scan):
        # No-``uusi`` fallback (old Pattern C, surface_parse line 2828): under a
        # ``lisätään`` verb the ``uusi`` may be left implicit, so a bare
        # ``DOC:ILL numlist (§ | luku)`` is still an insertion. Reproduced ONLY
        # for LISATA, re-scanning from the DOC:ILL anchor with NO comma /
        # reinstatement skip (the old fallback restores to the arm entry and
        # re-consumes the document fresh), so a ``DOC:ILL , …`` or any provenance
        # form is left to decline. The inserted entity returns to statute level.
        fb = _try_doc_ill_no_uusi(scan, saved, verb)
        if fb is not None:
            return fb
        scan.goto(saved)
        return None
    _skip_nain_kuuluva(scan)

    nums2 = _number_list(scan)
    if not nums2:
        scan.goto(saved)
        return None

    # Pre-``§`` chained ``[sekä/ja] uusi <nums>`` groups: the old Pattern C
    # (lines 2680-2689) collects every ``uusi <range>`` arm joined by a separator
    # before the closing structural noun into one batch (``lakiin uusi 5 ja 6
    # sekä uusi 7 ja 8 §``). Reproduce the collection so the closing ``§`` applies
    # to the whole list.
    all_nums = list(nums2)
    while True:
        saved_chain = scan.pos
        if _sep(scan) is not None and _consume_uusi(scan):
            more = _number_list(scan)
            if more:
                all_nums.extend(more)
                continue
        scan.goto(saved_chain)
        break

    t = scan.peek()
    if t is None:
        scan.goto(saved)
        return None
    if t.cat == "OSA":
        scan.advance()
        return [InsNode(kind=TargetKind.PART, label=n + sf, chapter="") for n, sf in all_nums]
    if t.cat in ("PYKALA", "LUKU") and t.case != "GEN":
        # Malformed ``§ luku`` chapter insert is out of scope.
        if t.cat == "PYKALA":
            t1 = scan.peek(1)
            if t1 is not None and t1.cat == "LUKU" and t1.case == "NOM":
                scan.goto(saved)
                return None
        kind = TargetKind.SECTION if t.cat == "PYKALA" else TargetKind.CHAPTER
        scan.advance()
        out_nodes = [InsNode(kind=kind, label=n + sf, chapter="") for n, sf in all_nums]
        # Post-``§`` NOM-section continuation: the old Pattern C (lines 2726-2764)
        # folds further ``[sekä/ja] [DOC:ILL] [uusi] <nums> § (NOM)`` whole-section
        # arms into the SAME batch (one shared witness span). Reproduce ONLY the
        # plain NOM-section arms. A trailing heading-placement / postfix-chapter
        # arm (which the old parser would ALSO fold into this batch) cannot have
        # its in-batch span reproduced here, so its presence forces a decline of
        # the whole insertion (cursor rewound to ``saved``). A §:ILL / §:GEN
        # sub-target continuation (which the old parser leaves for the outer loop)
        # is left un-folded: the cursor is rewound to just after the last folded
        # section so the driver's separator loop owns the next arm.
        if kind == TargetKind.SECTION:
            if not _fold_doc_section_continuation(scan, out_nodes):
                scan.goto(saved)
                return None
        return out_nodes

    # ``DOC:ILL uusi numlist §:GEN M momentti/kohta`` (old Pattern C, lines
    # 2766-2795): the genitive §:n is a sub-target insert into the single section.
    if t.cat == "PYKALA" and t.case == "GEN":
        saved_gen = scan.pos
        scan.advance()  # consume §:GEN
        sec_num = nums2[0][0] + nums2[0][1]
        sub_nums = _number_list(scan)
        st = scan.peek()
        if sub_nums and st is not None and st.cat in ("MOMENTTI", "KOHTA"):
            is_kohta = st.cat == "KOHTA"
            scan.advance()
            out: list[InsNode] = []
            for n, sf in sub_nums:
                for rn in _expand_range_single(n):
                    if is_kohta:
                        st_sub = InsSubTarget(momentti=1, item=rn + sf)
                    else:
                        st_sub = InsSubTarget(momentti=int(rn) if rn.isdigit() else 0)
                    out.append(
                        InsNode(kind=TargetKind.SECTION, label=sec_num, chapter="", sub_target=st_sub)
                    )
            return out
        # Plain stylistic ``DOC:ILL uuden N §:n`` variant (no momentti/kohta). The
        # old parser (line 2957) consumes the §:GEN and emits a plain
        # whole-section insert, one node per pre-§ number. Reproduce it: rewind to
        # the §:GEN, consume it, and emit whole-section nodes for ``all_nums``.
        scan.goto(saved_gen)
        scan.advance()  # consume §:GEN
        return [InsNode(kind=TargetKind.SECTION, label=n + sf, chapter="") for n, sf in all_nums]

    # Other GEN luku stylistic variants → out of scope for this slice.
    scan.goto(saved)
    return None


# ---------------------------------------------------------------------------
# Emitter — ParsedInsertion -> frozen SurfaceInsertion nodes.
#
# The witness is NOT attached here: the old parser stamps one shared batch
# witness (``_stamp_default_witness``) spanning the whole ``_target`` call, with
# a rule_id inferred from each node's shape. The driver owns that stamping so it
# can supply the batch span; this emitter produces witness-free nodes.
# ---------------------------------------------------------------------------


def emit_insertion_nodes(parsed: ParsedInsertion) -> list[SurfaceNode]:
    """Turn a recognized ``ParsedInsertion`` into witness-free SurfaceInsertions."""
    out: list[SurfaceNode] = []
    for node in parsed.nodes:
        sub_target = None
        if node.sub_target is not None:
            st = node.sub_target
            special = ""
            if st.facet == FacetKind.HEADING:
                special = "otsikko"
            elif st.facet == FacetKind.INTRO:
                special = "johd"
            sub_target = SurfaceSubRef(
                momentti=st.momentti, item=st.item, facet=st.facet, special=special
            )
        out.append(
            SurfaceInsertion(
                kind=node.kind,
                label=node.label,
                chapter=node.chapter,
                part=node.part,
                sub_target=sub_target,
            )
        )
    return out


def insertion_rule_id(node: SurfaceInsertion) -> str:
    """Infer the witness rule_id for an insertion node (mirrors stamping)."""
    if node.sub_target and node.sub_target.facet == FacetKind.HEADING:
        return "fi.insertion_heading"
    if node.sub_target and node.sub_target.momentti:
        return "fi.insertion_sub_target"
    if node.kind == TargetKind.SECTION:
        return "fi.insertion_section"
    if node.kind == TargetKind.CHAPTER:
        return "fi.insertion_chapter"
    return "fi.insertion_other"
