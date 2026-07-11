"""``lawvm fi-he-ir-compare`` — HE proposed-effect IR-EQUIVALENCE (PDF→ops vs XML→ops).

PHASE 2 of the structured-law goal.  Phase 1 (:mod:`lawvm.tools.fi_amendment_ir_compare`)
proved a *statute amendment's* PDF→IR path reproduces its trusted XML→IR path exactly.
Phase 2 inverts the source: an **HE** (Finnish government proposal) is itself the source
document, and its "effects on laws" are the PROPOSED amendment operations carried in its
bill text (lakiehdotus).  The product-level question is the same EXACTNESS one:

    does the HE PDF→proposed-ops path reproduce the trusted HE XML→proposed-ops path
    EXACTLY (op kind + target + proposed body), on the clean born-digital HE gold set?

Both witnesses are lowered through the IDENTICAL HE parser
(:func:`lawvm.finland.he_branch_parser._parse_one_clause`, the SAME clause grammar the
trusted XML path uses), so the op-level diff isolates PDF-text faithfulness:

    XML side (trusted reference):
        main.xml  --he_branch_parser.parse_he_branch-->  BranchProposedOp tuple
    PDF side  (path under test):
        main.pdf  --geom born-digital reading text-->  enacting-clause spans
                  --_parse_one_clause-->                BranchProposedOp tuple

HEs are BORN-DIGITAL prose, so the geom lane reads the PDF for FREE (no vision).

This is an EXACTNESS eval, not a fuzzy benchmark — there is no coverage/WER headline.
Every proposed op is either EXACTLY matched (same statute/provision target + same op
kind) or a TYPED :class:`OpDivergence` (reused from phase 1); then a PAYLOAD stage
compares each matched op's PROPOSED BODY TEXT modulo the legally-inert encoding quotient
(:mod:`lawvm.finland.op_equivalence`).  The result PASSes iff zero typed divergences.

Typed benign / deferred strata (a status on :class:`HECompareResult`, never a silent
empty — the same discipline as phase-1's ``xml_frame_only`` / ``pdf_annex_only``):

  * ``xml_wrapper_only`` — the HE main.xml is a WRAPPER pointing at a PDF (real content
    is PDF-only; a thin body + a ``.pdf``/``media`` component reference and no inline
    enacting clause).  The wrapper XML is NOT a trusted reference, so we do NOT diff it;
    its content is handled later by the PDF-as-source path.
  * ``not_applicable`` — the HE carries no enactment clauses at all (treaty ratification,
    budget, purely-rationale proposal): no proposed law effects to compare.
  * ``new_statute_only`` — every enacting clause ENACTS a brand-new law
    ("Eduskunnan päätöksen mukaisesti säädetään:"); there are no amendment operations
    against an existing statute, so the amendment-op reference is empty by construction.
  * ``xml_parse_incomplete`` — an amendment-verb enacting clause is present but the
    trusted XML parser lowered it to zero ops (an XML-side parse gap, not a PDF defect);
    deferred, never charged to the PDF path.
  * ``pdf_no_clause`` — the PDF reading text yielded no extractable enacting clause
    (born-digital lakiehdotus beyond the page window, a scanned HE the geom lane returns
    nothing for, or a corrupt-font text layer that renders to control codes — the last a
    VISION re-OCR escalation candidate the ingest suspect-region / cross-reader machinery
    owns, out of scope for this free-lane tool); deferred rather than forced into an
    all-ops-missing diff.  A no-clause HE is RETRIED once with reading-fidelity recoveries
    enabled (a text-layer glyph-substitution cite repair, a preceding-TOC-leader appendix
    uncut, and an annex/chapter target) before it is deferred — those recoveries are gated
    to this fallback so a normally-detected HE is byte-identical (0 collateral).

The extraction here (enacting-clause span segmentation + per-section body payloads) is
deliberately reusable toward the FULL-HE-structure goal (extracting every section — the
general + detailed perustelut AND the lakiehdotus bill texts — as verified IR); this
module's deliverable is the narrower proposed-EFFECT op equivalence.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, replace
from typing import Callable, Optional

from lawvm.finland.he_branch_parser import (
    HEParseStatus,
    HEParsedBranch,
    _AKN_NS,
    _element_text,
    _parse_one_clause,
    parse_he_branch,
)
from lawvm.finland.op_equivalence import text_equivalence
from lawvm.ingest.page_elements import dehyphenate
from lawvm.ingest.text_layer_repair import repair_glyph_substitution
from lawvm.tools.fi_amendment_ir_compare import DIVERGENCE_KINDS, OpDivergence

_DEFAULT_FARCHIVE = "data/fi_government_proposal.farchive"
_AKN_PATH_PREFIX = "akn/fi/doc/government-proposal/"

#: A comparison is a clean pass iff every divergence is "matched".
_BENIGN_MATCH = "matched"

#: Below this the HE main.xml body is a thin wrapper frame (real content is PDF-only).
_XML_WRAPPER_BODY_MAX_CHARS = 2000

#: A pathological giant HE main.pdf hangs the geom read (dense hundred-page PDFs);
#: above this byte size we TYPE it ``pdf_oversize`` and skip the read rather than
#: stall the whole corpus sweep — a logged, honest skip (NOT a silent cap), never a
#: diff. Normal HEs are a few MB; this only catches the rare outlier.
_MAX_HE_PDF_BYTES = 30_000_000

#: Non-compared terminal strata (never a genuine PDF defect).
_NON_COMPARED_STATUSES = (
    "xml_wrapper_only",
    "not_applicable",
    "new_statute_only",
    "xml_parse_incomplete",
    "pdf_no_clause",
    "pdf_oversize",
    "error",
)


# --------------------------------------------------------------------------- #
# Typed failures                                                              #
# --------------------------------------------------------------------------- #


class HEIrCompareError(Exception):
    """Base for all typed failures of the HE proposed-effect IR comparison."""


class HEReaderUnavailableError(HEIrCompareError):
    """The PDF text backend (``pypdfium2``) is not installed — the PDF witness cannot be
    read AT ALL on this machine.

    This is an ENVIRONMENT/configuration failure, categorically NOT a per-HE "no
    divergence" result: a missing backend yields NO witness, and absence of a witness must
    never be reported as absence of divergence. It is therefore surfaced DISTINCTLY (and
    propagated past the per-HE ``error`` typing in :func:`compare_he_from_farchive`) so a
    corpus sweep on a backend-less machine fails LOUDLY instead of typing every HE a benign
    read failure — which an aggregate would silently read as a clean/empty ("0 residual")
    pass, masking real divergences. Install the backend with ``uv sync --extra pdf``.
    """


# --------------------------------------------------------------------------- #
# Enacting-clause span extraction from PDF reading text (named recognizer).    #
# --------------------------------------------------------------------------- #
#
# A modern Finnish HE bill's enacting clause is the amendment directive
#   "<Eduskunnan|Valtioneuvoston|...> päätöksen mukaisesti muutetaan <lain (NUM/YEAR)>
#    <provisions> ... [ja lisätään ...] seuraavasti:"
# terminated by "... seuraavasti:".  Geom reading order over the lakiehdotus /
# rinnakkaistekstit page layout can REORDER the clause's lines (the centered
# "... päätöksen mukaisesti" formula lands mid-clause; a provenance segment
# "sellaisina kuin ne ovat, ..." can migrate onto the bill-title line), so we do NOT
# require a specific token order.  We anchor each span on its "... seuraavasti:"
# terminator, take a bounded window back, GATE it on the enactment formula being
# present (this rejects perustelut prose that merely contains an operative verb), and
# START the span at the earliest operative-verb-or-formula token in the window so a
# reordered "muutetaan ..." head is still captured.  Both witnesses then flow through
# the identical ``_parse_one_clause``.

_TERMINATOR_RE = re.compile(r"seuraavasti\s*:", re.IGNORECASE)

#: STRONG amendment-directive head verbs used to locate a clause span's START anchor.
#: Deliberately EXCLUDES bare "säädetään" / "siirretään" / "poistetaan": those are
#: ubiquitous in body prose ("tarkemmin säädetään valtioneuvoston asetuksella",
#: "säädetään rajavartiolain 34 g §:ssä"), so anchoring on them drags the span head far
#: back into prose and pulls a neighbouring statute citation into the clause → spurious
#: cross-statute ops.  The amendment heads below are rare in prose, and the START anchor
#: is additionally BOUNDED to within ``_VERB_HEAD_REACH`` chars of the enactment formula,
#: so only a genuine (possibly geom-reordered) "muutetaan ..." directive head is captured.
_HE_HEAD_VERB_RE = re.compile(
    r"\b("
    r"muut(?:etaan|tanut|ettu)|"
    r"lis[äa]t[äa]{1,2}n|"
    r"lis[äa](?:nnyt|tty)|"
    r"kumo(?:taan|nnut|ttu)|"
    r"korv(?:ataan|annut|attu)"
    r")\b",
    re.IGNORECASE,
)

#: A bare Finnish statute citation "(NUM/YEAR)" — the target-statute anchor an amendment
#: directive head must be followed by (the parser resolves the op targets against it).
#: The year is FOUR digits in the modern convention ("(758/1989)") but TWO in the pre-2000
#: typographic convention the 1990s HEs used ("(178/76)", "(452/72)"); a bare 2-digit-year
#: cite defeated this anchor on the whole old-format stratum (14 pdf_no_clause HEs, all
#: 1992–1999), so the enacting clause was never detected. Admitting the 2-digit form recovers
#: it. This is ONLY the span-detection ANCHOR (an existence check): the span text still flows
#: through the SHARED :func:`_parse_one_clause`, whose statute resolver keeps the 4-digit form
#: — so a 2-digit-cite HE resolves an EMPTY statute id on BOTH witnesses identically (the XML
#: johtolause carries the same 2-digit cite) and the ops match on their provision paths. A
#: 3-digit year is not a real statute year, so it is excluded (``\d{2}|\d{4}``).
_CITE_RE = re.compile(r"\(\d{1,5}/(?:\d{2}|\d{4})\)")

#: Max distance from an amendment-verb head to the statute citation it governs. Must
#: span a full Finnish law TITLE, which for EU-implementation acts is long ("lisätään
#: hallinnollisesta yhteistyöstä verotuksen alalla … annettuun lakiin (185/2013)" ≈ 238
#: chars) — at 160 the second bill of a multi-bill HE was dropped (op_missing). The head
#: verb matches only enacting-PRESENT forms (not the conditional perustelut discuss in),
#: and a "§" + "seuraavasti" are still required in-window, so a wide title budget does not
#: admit perustelut prose.
_HEAD_TO_CITE = 400

#: The Finnish amendment-HISTORY marker that opens a provenance sub-clause: "sellaisena kuin
#: se on …" / "sellaisina kuin ne ovat …" / "sellaisina kuin niistä ovat …" = "as it stands,
#: [as] amended by acts …".  Everything AFTER this marker, up to the directive's terminator
#: (the next amendment-verb head or "seuraavasti:"), is a list of the PRIOR AMENDING acts of
#: the sections being touched — NOT the governing (amended) act.  Those ids are frequently
#: parenthesised ("… ja (668/2013), 80 § …"), so a naive "(NUM/YEAR)" head-cite anchor picks
#: the MOST RECENT amending act as a phantom op TARGET (HE 139/2013: 26 phantom ops on 668/2013,
#: the whole ulkomaalaislaki 301/2004 provision list mis-attributed to a history id).  A cite
#: that lies past this marker within a directive is excluded from head-cite / bill-scope
#: resolution (:func:`_governing_cite_after`).  Purely PDF-structural; never reads the XML.
_HISTORY_MARKER_RE = re.compile(r"sellais(?:ena|ina)\s+kuin", re.IGNORECASE)

#: A provision marker ("§") — a genuine amendment directive lists the provisions it
#: touches (7 §, 9 §:n 2 momentti, ...) between its statute citation and "seuraavasti:".
#: Requiring one INSIDE the candidate span is the structural discriminator that separates
#: an enacting clause from a stray perustelut sentence ("muutetaan lakia X merkittävästi
#: ... seuraavasti:") — and, unlike the enactment formula, it is ALWAYS co-located with
#: the clause, so geom scattering the centered "... päätöksen mukaisesti" formula far from
#: its clause does not cause the (genuine) clause to be dropped.
_PROVISION_MARK_RE = re.compile(r"§")

#: A WHOLE-ANNEX / WHOLE-CHAPTER amendment target — the one genuine johtolause shape that lists
#: NO "§": "muutetaan … lain (N/YEAR) liite … seuraavasti:" (replace an annex, HE 151/2013) and
#: "lisätään … (N/YEAR) … uusi 4 a luku seuraavasti:" (insert a chapter, HE 101/2020). Both lower
#: to a clean op ("1471/1994"; "1227/2016/4a"), but the §-only provision guard
#: (:data:`_PROVISION_MARK_RE`) dropped the whole clause. Accepted ONLY as a FALLBACK when no "§"
#: is present in the same head→terminator window (a §-listing directive is unaffected), and the
#: word must be the nominative annex/chapter target ("liite"/"luku"), never an inflected
#: cross-reference ("5 luvun", "liitteessä"), so a perustelut sentence mentioning a chapter in
#: passing is not admitted. Flat/bounded quantifiers (FW-07); local to
#: :func:`extract_enacting_clause_spans` (the body-scoping recognizers stay §-strict).
_ANNEX_CHAPTER_TARGET_RE = re.compile(r"\bliite\b|\bluku\b", re.IGNORECASE)

#: The enactment FORMULA token ("<Eduskunnan|Valtioneuvoston|…ministeriön> päätöksen
#: mukaisesti") that introduces every genuine johtolause.  It is NOT required in general (geom
#: can scatter the centered formula far from its clause — see
#: :func:`extract_enacting_clause_spans`), but it is the corroboration demanded of the one
#: AMBIGUOUS head verb, "korvataan" (:func:`_korvataan_head_is_directive`).  Matched by
#: ``str.find`` on a lowercased window (no regex — the same flat-census discipline as
#: :data:`_LAKIEHDOTUS_HEADING` / :data:`_ASETUSLUONNOS_HEADING`); the flattened reading text
#: collapses whitespace runs to single spaces, so the single-space form is reliable.
_ENACTMENT_FORMULA = "päätöksen mukaisesti"

#: How far BEFORE a "korvataan" head, and AFTER its citation, the enactment formula is
#: accepted as corroboration (a genuine johtolause prints "… päätöksen mukaisesti korvataan
#: … (N/YEAR) … seuraavasti:", the formula abutting the head; geom may reorder the centered
#: formula onto the line just AFTER the provision list, hence the small forward window too).
_KORVATAAN_FORMULA_REACH = 300
_KORVATAAN_FORMULA_AFTER_CITE = 60

#: Any amendment verb (vs the new-law-only "säädetään"): tells a proposed-AMENDMENT HE
#: apart from a pure new-statute enactment when the XML lowers to zero ops.
_AMEND_VERB_RE = re.compile(
    r"\b(muut(?:etaan|tanut|ettu)|lis[äa]t|kumo|korv|poist|siirr)", re.IGNORECASE
)


def _korvataan_head_is_directive(flat: str, hstart: int, cite_end: int) -> bool:
    """True iff an ambiguous ``korvataan`` head is a genuine amendment directive, not body prose.

    ``korvataan`` is the ONE amendment-head verb (:data:`_HE_HEAD_VERB_RE`) that is ALSO
    pervasive body prose.  As a directive it means "is replaced" (rare — a replacement is
    normally written ``muutetaan``); as substantive prose it means "is reimbursed / compensated",
    which saturates social-insurance and health-law bill BODIES ("… sairaanhoidon kustannuksia,
    jos kustannukset korvataan vankeuslain (767/2005) 10 luvun 7 §:n perusteella; 7) …").  Such
    a body cross-reference carries an amendment verb + a parenthesised statute citation + "§" +
    a downstream "seuraavasti:", so it MIMICS an enacting clause and was lowered as a phantom
    amendment of the merely CROSS-REFERENCED statute (HE 103/2013: 12 phantom ops on vankeuslaki
    767/2005 — a law the HE never amends — that the reclassifier then force-typed benign
    ``pdf_out_of_scope_statute``, masking a target-misresolution defect).

    Unlike ``muutetaan`` / ``lisätään`` / ``kumotaan`` — rare in prose, so admitted with no
    corroboration (a genuine clause needs no formula; geom may scatter it) — a ``korvataan`` head
    is admitted ONLY when the enactment FORMULA (:data:`_ENACTMENT_FORMULA`) corroborates it:
    just BEFORE the head (the formula abutting it) or just AFTER its citation (geom reordered the
    centered formula past the provision list).  Purely PDF-structural; never reads the XML.
    """
    lo = max(0, hstart - _KORVATAAN_FORMULA_REACH)
    if _ENACTMENT_FORMULA in flat[lo:hstart].lower():
        return True
    return _ENACTMENT_FORMULA in flat[cite_end : cite_end + _KORVATAAN_FORMULA_AFTER_CITE].lower()


def _governing_cite_after(flat: str, lo: int, hi: int) -> "Optional[re.Match[str]]":
    """First GOVERNING statute cite "(NUM/YEAR)" in ``flat[lo:hi]``, skipping history-list ids.

    The GOVERNING (amended) act of a johtolause directive is cited right after the verb head
    and its law-name — BEFORE any "sellaisena kuin se on … / sellaisina kuin ne ovat …"
    amendment-history sub-clause (:data:`_HISTORY_MARKER_RE`).  Every parenthesised id that
    appears AFTER that marker within the directive is a PRIOR AMENDING act of the touched
    sections, not the target (e.g. "muutetaan 3 §:n 6 ja 7 kohta … sellaisina kuin niistä ovat,
    73 § osaksi laissa 449/2012, 79 § laeissa … ja (668/2013), …": (668/2013) is merely the most
    recent amending act).  We therefore return the first ``(NUM/YEAR)`` with NO history marker
    between ``lo`` and the cite — the genuine governing anchor — and ``None`` when a directive
    head carries ONLY history-list ids (a same-bill continuation verb whose governing act was
    named by an earlier head; the earlier head's span already covers these provisions).

    Purely PDF-structural (it reads only the johtolause word order), so it never risks dropping
    a real SECOND bill: a real bill prints its own "muutetaan <name> (id)" with the governing id
    BEFORE any "sellaisina", which this returns unchanged.
    """
    for cite in _CITE_RE.finditer(flat, lo, hi):
        if _HISTORY_MARKER_RE.search(flat, lo, cite.start()) is None:
            return cite
    return None


def _is_continuation_head(flat: str, hstart: int, scan_lo: int) -> bool:
    """True iff an amendment-verb head at ``hstart`` continues an already-open johtolause.

    A CONTINUATION head is preceded — since the last "seuraavasti:" terminator (or the scan
    start) — by another amendment-verb head that opened the current johtolause: the two share a
    directive block ("kumotaan (id) … muutetaan … lisätään … seuraavasti:").  The opening head's
    span already runs to the shared terminator and covers this head's provisions, so a
    continuation head that carries no governing cite of its own is a redundant duplicate.  An
    OPENING head (none precedes it in the block) is NOT a continuation and is kept.
    """
    prev_term = None
    for t in _TERMINATOR_RE.finditer(flat, scan_lo, hstart):
        prev_term = t
    block_lo = prev_term.end() if prev_term is not None else scan_lo
    return _HE_HEAD_VERB_RE.search(flat, block_lo, hstart) is not None


#: Bounded window back from a "... seuraavasti:" terminator (AGENTS.md §1.11 bound).
_MAX_CLAUSE_CHARS = 2400

#: Widened head→"seuraavasti:" bound used ONLY once the scan is anchored at the genuine
#: "Lakiehdotukset" bills-section heading (:func:`_lakiehdotus_scan_start`). A STRUCTURAL
#: mega-amendment johtolause enumerates hundreds of provisions and runs 3k–13k chars to its
#: terminator, overflowing the narrow default → the whole clause (and every op it carries) was
#: silently dropped (the dominant mega-omnibus op_missing cause). The narrow default cannot
#: simply be raised: a wide window over the FULL region lets detailed-perustelut prose (which
#: repeats the amendment-verb + citation + "§" + "seuraavasti" signature) be mis-read as
#: clauses (op_extra explosion). Anchoring at the bills heading FENCES the perustelut out, so
#: the bound can be widened here without that regression. Set just above the largest real
#: johtolause (~13.4k over the census) with margin, yet finite so a terminator-less head cannot
#: grab an arbitrarily distant terminator. Bounded (FW-07).
_LAKIEHDOTUS_SCAN_BOUND = 16000

#: The genuine bills-section heading token a modern multi-bill HE prints its lakiehdotus
#: directives under ("Lakiehdotukset 1. Laki …", nominative plural). Matched by ``str.rfind``
#: (no regex — keeps the semantic-plane regex census flat, the same discipline as
#: :func:`_resolve_span_end` / :func:`_statute_id_of`).
_LAKIEHDOTUS_HEADING = "Lakiehdotukset"

#: End-of-lakiehdotus marker: after the bill directives an HE carries the
#: Rinnakkaistekstit (parallel-texts appendix — a two-column
#: "Voimassa oleva laki | Ehdotus" reprint of every amended law) and the Liitteet.
#: Those appendices reprint amendment-verb + citation + "§ ... seuraavasti"
#: signatures that flatten into SPURIOUS enacting-clause spans resolving to extra
#: (often foreign-statute) targets — the dominant op_extra source. We bound the
#: clause scan to the text BEFORE the first such heading. Only the UNAMBIGUOUS
#: section headings match — "Rinnakkaisteksti(t)" and the PLURAL "Liitteet" — never
#: bare "Liite", which occurs in genuine bill text ("muutetaan liite 1 ...").
#: Flat quantifiers only (FW-07).
_LAKIEHDOTUS_END_RE = re.compile(r"\b(?:Rinnakkaisteksti[a-zä]{0,4}|Liitteet)\b")

#: Minimum dots that mark a TABLE-OF-CONTENTS dotted leader ("Rinnakkaistekstit . . . . 41").
#: A genuine sentence following a heading never opens with a run of bare dots, so four is an
#: ample, false-positive-safe floor.
_TOC_LEADER_MIN_DOTS = 4


#: A table-of-contents dotted-leader RUN ("muuttamisesta . . . . . . 30"), space-tolerant.
#: Used to convict an appendix heading that a PREVIOUS TOC line's leader immediately precedes.
_TOC_LEADER_RUN_RE = re.compile(r"\.(?:\s?\.){%d,}" % (_TOC_LEADER_MIN_DOTS - 1))

#: How far BEFORE an appendix heading a preceding TOC dotted leader may sit and still convict it
#: as a front-matter entry (the previous line's "<title> . . . . <page> LIITE" furniture).
_TOC_LEADER_BEFORE_REACH = 60


def _toc_leader_precedes(flat: str, pos: int) -> bool:
    """True iff a table-of-contents dotted-leader run sits just BEFORE ``flat[pos]``.

    The genuine appendix heading is preceded by the last bill's substantive text / signature
    block (prose, never a dot run); a TABLE-OF-CONTENTS entry is preceded by the PREVIOUS
    entry's dotted leader and page number ("… muuttamisesta . . . . . . 30 LIITE
    Rinnakkaisteksti"). This catches the TOC entry whose OWN leader trails the bill TITLE it
    names ("Rinnakkaistekstit 1. Laki … muuttamisesta . . . 37"), which
    :func:`_starts_with_toc_leader` (an immediately-after check) misses. Purely PDF-structural.
    """
    # lawvm-regex: witness_only PDF-witness structural anchor (TOC dotted-leader run); never reads XML.
    return _TOC_LEADER_RUN_RE.search(flat[max(0, pos - _TOC_LEADER_BEFORE_REACH):pos]) is not None


def _starts_with_toc_leader(flat: str, pos: int) -> bool:
    """True iff ``flat[pos:]`` opens with a table-of-contents dotted-leader run.

    A TOC entry prints its label then a dotted leader to the page number
    ("Rinnakkaistekstit . . . . . . 41"); a GENUINE body appendix heading is immediately
    followed by parallel-text content ("Voimassa oleva laki | Ehdotus …"), never a leader.
    So a leader immediately trailing an appendix-heading match convicts it as a front-matter
    TOC entry. Bounded MANUAL scan (no regex — the flat-census / FW-07 discipline of
    :func:`_numbered_bill_follows` / :func:`_lakiehdotus_scan_start`): count dots, tolerate the
    single spaces the flattener leaves between them, stop at the first other char. A run of
    ``_TOC_LEADER_MIN_DOTS`` dots convicts; anything else (a real word, a page digit, a stray
    single dot) does not.
    """
    dots = 0
    for c in flat[pos : pos + 256]:
        if c == ".":
            dots += 1
            if dots >= _TOC_LEADER_MIN_DOTS:
                return True
        elif c != " ":  # a leader tolerates the single spaces between its dots
            return False
    return False


def _first_appendix_end(flat: str, *, aggressive: bool = False) -> "Optional[re.Match[str]]":
    """First GENUINE appendix-heading match in ``flat``, skipping table-of-contents entries.

    :data:`_LAKIEHDOTUS_END_RE` locates the "Rinnakkaistekstit"/"Liitteet" heading that opens
    the post-bill appendix. In an old-format HE the same word FIRST appears in the front-matter
    table of contents ("… Rinnakkaistekstit . . . . 41"), and cutting there dropped the entire
    bill body (4 of the 14 pdf_no_clause HEs). A TOC entry is distinguished PURELY STRUCTURALLY
    by the dotted leader immediately trailing its label (:func:`_starts_with_toc_leader`),
    whereas a real body heading is followed by the parallel-text reprint. We return the first
    match that is NOT a TOC entry, or ``None`` if every match is one (then the region is left
    uncut — correct: the real appendix is beyond the page window or absent, bills stay in scope).
    """
    # lawvm-regex: witness_only PDF-witness structural anchor (appendix heading enumeration); never reads XML.
    for m in _LAKIEHDOTUS_END_RE.finditer(flat):
        # A TOC entry is convicted by a dotted leader trailing its label (an old-format HE
        # lists "Rinnakkaistekstit … 41" — the always-on check). The FALLBACK adds the
        # preceding-leader signal, which catches a TOC entry whose OWN leader trails the bill
        # TITLE it names ("Rinnakkaistekstit 1. Laki … muuttamisesta . . . 37") — that cut the
        # whole bill body to the cover page (HE 47/1999, 73/1996, 82/1997). Gated to the fallback
        # so it can never skip a genuine appendix cut on a currently-detected HE.
        if _starts_with_toc_leader(flat, m.end()) or (
            aggressive and _toc_leader_precedes(flat, m.start())
        ):
            continue
        return m
    return None


#: The editorial section heading a multi-instrument HE prints its DRAFT-DECREE
#: (asetusluonnos) proposals under — the decrees sit AFTER the law-bills and their
#: rinnakkaistekstit appendix, so :data:`_LAKIEHDOTUS_END_RE` truncates them away with the
#: appendix (their genuine "muutetaan … (N/YEAR) … § … seuraavasti:" directives were dropped
#: as op_missing while the XML witness parses them).  Matched by ``str.find`` (no regex — the
#: same discipline as :data:`_LAKIEHDOTUS_HEADING`).  A draft decree's enacting clause carries
#: the SAME amendment-directive grammar as a law bill; only its enactment FORMULA differs — a
#: decree is issued "Valtioneuvoston/…ministeriön päätöksen mukaisesti" (never the law bills'
#: "Eduskunnan päätöksen mukaisesti"), which is the structural discriminator below.
_ASETUSLUONNOS_HEADING = "Asetusluonnokset"

#: A DRAFT-DECREE amendment johtolause head: the decree enactment formula immediately
#: followed by a STRONG amendment verb.  The formula ("Valtioneuvoston …" / a
#: "…ministeriön päätöksen mukaisesti") is unique to a proposed asetus and never appears in a
#: law bill or its rinnakkaisteksti (those read "Eduskunnan päätöksen mukaisesti"), so this is
#: the structural anchor that (a) CONFIRMS a genuine draft-decree section (rejecting a stray
#: "Asetusluonnokset" heading word) and (b) marks where in that section the amendment decrees
#: begin — a leading NEW-decree ("… päätöksen mukaisesti säädetään … nojalla:") carries no
#: amendment op, so ``säädetään`` is deliberately EXCLUDED here: the appended region starts at
#: the FIRST amendment decree, keeping a new-decree's provision bodies out of the scan. The
#: amend-verb list mirrors :data:`_HE_HEAD_VERB_RE`. Flat/bounded quantifiers only (FW-07).
_DECREE_ENACT_RE = re.compile(
    r"(?:Valtioneuvoston|ministeri[öo]n)\s{1,4}p[aä]{1,2}t[öo]ksen\s{1,4}mukaisesti\s{1,4}"
    r"(?:muut(?:etaan|ettu)|lis[äa]t[äa]{1,2}n|kumo(?:taan|ttu)|korv(?:ataan|attu))",
    re.IGNORECASE,
)

#: The per-page running header "HE <n>/<year> vp" (with its adjacent page number) that the
#: text layer emits at every page top/bottom. It lands MID-body when a provision spans a
#: page break ("…joka 4 HE 84/1998 vp omistajan…"), breaking payload equality. It is never
#: part of a proposed body, so deleting it (and the immediately-adjacent page digits) is
#: safe. Bounded/flat quantifiers (FW-07). "HE n/year vp" ≠ a "(n/year)" statute citation,
#: so this does not touch the enacting-clause citation anchor. The SECOND alternative is the
#: DASH form the lakiehdotus reprint carries at each page top — "<YEAR> vp - HE <NUM> <page>"
#: ("1992 vp - HE 231 3", "1993 vp - HE 285 7") — same running-header furniture, opposite
#: token order and no "/year"; its trailing digits are the liite / page number.
#:
#: This header is stripped BEFORE de-hyphenation (see :func:`_flatten_reading_text`), for the
#: SAME reason as the signature-date furniture below: when a word wraps at a PAGE break the
#: header is emitted BETWEEN the trailing hyphen and its continuation ("eyden jär-\n2 HE
#: 195/1996 vp\njestämisestä" for "järjestämisestä"). If the header is stripped only AFTER
#: de-hyphenation, de-hyphenation sees the header's leading page digit right after the hyphen
#: ("jär-\n2…"), mis-reads it as a NUMERIC compound seam ("40-vuotias") and KEEPS the hyphen;
#: the later strip then leaves a spurious "jär- jestämisestä" residual (the residual-hyphen-
#: at-seam payload_mismatch stratum). Stripping the header first lets the trailing hyphen abut
#: its real continuation so the EXISTING corroboration gate in ``dehyphenate`` decides it
#: correctly — fusing the wrapped word and PRESERVING a genuine compound ("maa- ja",
#: "rakennus- tai", which are NOT header-interleaved and survive untouched). The residual
#: single mid-word space the " " replacement leaves ("jär jestämisestä") is then folded by the
#: adjudicator-proven WHITESPACE_MIDWORD quotient at comparison time.
_PAGE_FURNITURE_RE = re.compile(
    r"(?:\d{0,4}\s{0,3}HE\s{1,3}\d{1,4}/\d{4}\s{1,3}vp\s{0,3}\d{0,4}"
    r"|\d{4}\s{1,3}vp\s{0,3}-?\s{0,3}HE\s{1,3}\d{1,4}\s{0,3}\d{0,4})",
    re.IGNORECASE,
)

#: The Helsinki signature-DATE line ("Helsingissä 9 päivänä lokakuuta 1992") the centered
#: enacting furniture carries between the President's name and the bill body. On a two-column
#: lakiehdotus reprint the text layer SCATTERS this centered line INTO a mid-column word,
#: splitting an end-of-line hyphenation ("työnan-<Helsingissä 9 päivänä lokakuuta 1992>tajanaan
#: …") so de-hyphenation can no longer rejoin "työnantajanaan". It is pure enacting furniture,
#: never provision text, so it is deleted BEFORE de-hyphenation (letting the split word rejoin).
#: The day digit + "…kuuta" month + 4-digit year make it specific enough to never hit body
#: prose. Flat/bounded quantifiers (FW-07). The trailing ``\s{0,4}`` swallows the line break
#: that followed the scattered furniture so the split hyphen glyph ("työnan¬<date>¬tajanaan")
#: abuts its continuation ("työnan¬tajanaan") and de-hyphenation can fuse "työnantajanaan";
#: the phrase is replaced by "" (not a space) for the same reason.
_SIGNATURE_DATE_RE = re.compile(
    r"Helsingiss[aä]\s{0,3}\d{1,2}\s{0,3}p[aä]iv[aä]n[aä]\s{0,3}[a-zäö]{3,12}kuuta\s{0,3}\d{4}\s{0,4}",
    re.IGNORECASE,
)

#: A CHAPTER ordinal glued to its "luku"/"luvu" chapter noun ("15lukuun", "16luvun") — a geom
#: text-layer artifact that drops the thin space between the number and the chapter word in a
#: two-column lakiehdotus reprint.  The XML clause always spaces it ("15 lukuun"), so the shared
#: johtolause lexer tokenizes the XML "15" + "lukuun" as NUM + LUKU (chapter scope resolved) but
#: the glued PDF "15lukuun" as a single opaque WORD — dropping the chapter, so the op target reads
#: bare ("39/1889/12a" vs XML "39/1889/luku_15/12a") and never pairs (double-counted op_missing +
#: op_extra).  Restoring the missing space is a PDF-reading-faithfulness fix on PDF-STRUCTURAL
#: signal only (a digit abutting the chapter-noun stem is never a legitimate token); it lets BOTH
#: witnesses flow through the IDENTICAL lexer with the chapter intact, and never reads the XML
#: answer key.  Fixed-width look-behind + bounded stem alternation, no variable quantifiers
#: (FW-07); the zero-width insert leaves an already-spaced "15 luvun" untouched.
_PDF_GLUED_CHAPTER_RE = re.compile(r"(?<=\d)(?=luku|luvu)", re.IGNORECASE)

#: A statute citation "(NUM/YEAR)" whose SLASH glyph the text layer mis-read as a digit "1".
#: In a stratum of 1990s HEs the "/" in a parenthesised cite renders as "1" in the embedded
#: font ("(1505/1992)" → "(150511992)", "(543/1994)" → "(54311994)", "(704/75)" →
#: "(704175)"), so the cite ANCHOR (:data:`_CITE_RE`) never fires and the whole enacting clause
#: is dropped (pdf_no_clause) even though the johtolause is fully present. We REPAIR the token
#: back to "(NUM/YEAR)" so BOTH the anchor AND the shared :func:`_parse_one_clause` resolver see
#: the correct citation — and, crucially, resolve it IDENTICALLY to the XML witness (whose
#: johtolause carries the clean "(NUM/YEAR)"): a 4-digit-year cite resolves the same statute id
#: on both, and a 2-digit-year cite that the shared resolver leaves EMPTY does so on both (the
#: XML op then also carries an empty statute id — they still match on the provision path). This
#: is a PDF-reading-faithfulness repair on PDF-STRUCTURAL signal only; it never reads the XML.
#:
#: SAFETY: the YEAR is anchored to the token's LAST digits (4-digit form first) and constrained
#: to a real statute-year band (1600–2099) by :func:`_cite_year_band_plausible`, and the slash
#: surrogate is the single "1" that must sit immediately before it — so the split is unambiguous
#: and a parenthesised non-citation number (a monetary figure, a long id) whose trailing digits
#: are not a plausible year is left untouched. The 4-digit form runs first (tighter, band-gated);
#: the 2-digit fallback recovers the pre-2000 typographic convention ("(704/75)"). Flat/bounded
#: quantifiers (FW-07).
#:
#: The restore-then-VALIDATE mechanic is jurisdiction-agnostic and lives in
#: :func:`lawvm.ingest.text_layer_repair.repair_glyph_substitution`; only the "(NUM/YEAR)" cite
#: SHAPE (below) and the 1600–2099 YEAR BAND (the validator) are FI/EU-citation-specific surface.
#: The 4-digit shape captures the trailing 4 digits as the year; the band check is the validator,
#: not baked into the pattern, so this token repair is ONE registered caller of the general seam.
_CITE_SLASH_AS_ONE_4_RE = re.compile(r"\((\d{1,4})1(\d{4})\)")
_CITE_SLASH_AS_ONE_2_RE = re.compile(r"\((\d{1,4})1(\d{2})\)")

#: The FI/EU statute-year band the restored 4-digit cite must sit in (1600–2099) for the slash
#: repair to be adopted — the independent validator that keeps a parenthesised monetary figure /
#: long id from being mangled into a phantom citation.
_CITE_YEAR_LO, _CITE_YEAR_HI = 1600, 2099


def _cite_year_band_plausible(match: "re.Match[str]") -> bool:
    """Is the restored cite's 4-digit YEAR (group 2) inside the statute-year band? (validator)."""
    return _CITE_YEAR_LO <= int(match.group(2)) <= _CITE_YEAR_HI


def _repair_slash_as_one_cites(text: str) -> str:
    """Restore the "/" a text layer rendered as "1" inside a statute citation (see above).

    Delegates the restore-then-validate mechanic to the general
    :func:`~lawvm.ingest.text_layer_repair.repair_glyph_substitution` seam, supplying the
    FI/EU cite SHAPE + the "/"↔"1" confusion + the 1600–2099 year-band VALIDATOR (4-digit form),
    then the 2-digit-year fallback (whose 2-digit tail carries no band constraint).
    """
    # lawvm-regex: witness_only PDF-witness glyph-substitution repair (cite slash-as-"1"); never reads XML.
    text = repair_glyph_substitution(
        text,
        corrupt_re=_CITE_SLASH_AS_ONE_4_RE,
        restore=r"(\1/\2)",
        is_plausible=_cite_year_band_plausible,
    )
    # lawvm-regex: witness_only PDF-witness glyph-substitution repair (2-digit-year form); never reads XML.
    return repair_glyph_substitution(
        text, corrupt_re=_CITE_SLASH_AS_ONE_2_RE, restore=r"(\1/\2)"
    )


#: A private-use sentinel marking where an isolated mid-body page-number line was removed (i.e. a
#: PAGE BREAK fell inside a section body).  Used ONLY by :func:`_pdf_proposed_bodies`: when a
#: dash-elided body SPANS one, the capture stitched text across a page break whose text layer may
#: carry an unresolved hyphen seam or scattered furniture ("…Pää-<hdr><pagenum>töksen…" for
#: "Päätöksen"); such a body is DEFERRED, never forced into a payload_mismatch (AGENTS.md: a
#: segmentation / extraction miss is a deferral, never a "the HE proposes different text" diff).
_PDF_PAGE_BREAK_SENTINEL = ""

#: An ISOLATED page-number line ("…eläkelain mukaisen\n119\nvanhuuseläkkeen…") the text layer
#: emits mid-body at a page break.  It is distinct from the running header (:data:`_PAGE_FURNITURE_RE`)
#: and survives it when a blank line separates the two (the header's tight trailing bound cannot
#: reach across it).  Captured WITH its preceding line so a genuine REFERENCE number ("§:n 1
#: momentissa", "1 momentin 3 kohta" — preceded by a section marker or a legal-unit word, via
#: :data:`_PDF_REF_NUMBER_CONTEXT_RE`) is KEPT while a page number (preceded by ordinary prose) is
#: dropped.  The following-content guard requires prose / a "N)" marker after the digit line so a
#: section header ("14 §") or a wrapped list marker ("2\n)") is never eaten.  Flat/bounded (FW-07).
_PDF_PAGE_NUMBER_LINE_RE = re.compile(
    r"([^\n]{0,24})\n[ \t\r]*\d{1,4}[ \t\r]*\n"
    r"(?=[ \t\r]*(?:[a-zà-öø-ÿ]|\d{1,2}\s{0,2}\)))"
)
_PDF_REF_NUMBER_CONTEXT_RE = re.compile(
    r"(?:§[:a-zäö]{0,4}|moment\w*|kohd\w*|pykäl\w*|luvu\w*|luku)\s*$", re.IGNORECASE
)


def _strip_page_number_lines(reading_text: str) -> str:
    """Replace isolated mid-body page-number lines with :data:`_PDF_PAGE_BREAK_SENTINEL`.

    A legal REFERENCE number (preceded by a section marker / legal-unit word) is KEPT; only a
    genuine PAGE number (preceded by ordinary prose) is removed.  Applied ONLY on the body-
    segmentation path (:func:`_pdf_proposed_bodies`), so the shared op-segmentation flatten and
    its de-hyphenation are untouched.
    """

    def repl(m: "re.Match[str]") -> str:
        if _PDF_REF_NUMBER_CONTEXT_RE.search(m.group(1)):
            return m.group(0)  # a legal reference number, not page furniture
        return m.group(1) + "\n" + _PDF_PAGE_BREAK_SENTINEL + "\n"

    return _PDF_PAGE_NUMBER_LINE_RE.sub(repl, reading_text or "")


def _flatten_reading_text(reading_text: str, *, aggressive: bool = False) -> str:
    """De-hyphenate, strip per-page running headers, and whitespace-flatten reading text.

    ``aggressive`` enables the reading-fidelity REPAIRS that recover an otherwise-undetectable
    enacting clause (a text-layer glyph-substitution fix — the slash-as-"1" citation, see
    :func:`_repair_slash_as_one_cites`). It is OFF by default and is only ever set on the
    :func:`compare_he` FALLBACK for an HE the normal path found NO clause in, so a currently-
    detected HE's text is byte-identical and cannot be perturbed (0 collateral). The repair is a
    correct read, but it also surfaces additional GENUINE content on already-detected HEs
    (secondary decree bills, extra cross-references) that shifts their divergence set — gating it
    to the no-clause fallback keeps the recovery from disturbing the working corpus.

    BOTH page furniture classes — the centered signature-date line
    (:data:`_SIGNATURE_DATE_RE`) and the per-page running header (:data:`_PAGE_FURNITURE_RE`)
    — are stripped BEFORE de-hyphenation, because either can be emitted BETWEEN a wrapped
    word's trailing hyphen and its continuation at a column/page break. Removing them first
    lets the trailing hyphen abut its real continuation so ``dehyphenate``'s corroboration
    gate resolves it correctly (fuse a wrapped word, PRESERVE a genuine compound) instead of
    mis-reading the header's leading page digit as a numeric compound seam and stranding a
    spurious "word- word" residual. The header is replaced by a SPACE (not ""): unlike the
    hyphen-adjacent case, an INLINE header glues its neighbours if deleted, so the space is
    kept and the one residual mid-word space it leaves at a hyphen seam ("jär jestämisestä")
    is folded by WHITESPACE_MIDWORD at comparison time.
    """
    text = _SIGNATURE_DATE_RE.sub("", reading_text or "")
    text = _PAGE_FURNITURE_RE.sub(" ", text)
    text = dehyphenate(text)
    text = re.sub(r"[ \t\r\n­]+", " ", text).strip()
    # Un-glue a chapter ordinal welded to its "luku"/"luvu" noun ("15lukuun" → "15 lukuun"),
    # a geom text-layer artifact, so the shared lexer resolves the chapter scope on the PDF
    # witness exactly as it does on the XML witness (see :data:`_PDF_GLUED_CHAPTER_RE`).
    text = _PDF_GLUED_CHAPTER_RE.sub(" ", text)
    # FALLBACK only: repair a statute citation whose "/" the text layer rendered as "1"
    # ("(150511992)" → "(1505/1992)"), so the cite anchor fires and the shared parser resolves it
    # exactly as the XML witness does (see :func:`_repair_slash_as_one_cites`).
    if aggressive:
        text = _repair_slash_as_one_cites(text)
    return text


def _asetusluonnos_region(flat: str, lo: int) -> str:
    """The DRAFT-DECREE (asetusluonnos) directive block in ``flat[lo:]``, or "" if none.

    In a multi-instrument HE the proposed decrees (asetusluonnokset) are printed UNDER their
    own section heading AFTER the law-bills' rinnakkaistekstit appendix — i.e. after the
    :data:`_LAKIEHDOTUS_END_RE` cut ``lo``.  They carry the identical
    "muutetaan … (N/YEAR) … § … seuraavasti:" amendment grammar the XML witness parses, so
    dropping them with the appendix strands their ops as op_missing.  This recovers ONLY that
    block, LABEL-INDEPENDENTLY (never reads the XML answer key):

      * The genuine section is fixed by the "Asetusluonnokset" editorial heading (``str.find``
        after the appendix cut, so a table-of-contents entry that precedes the cut is skipped).
      * A stray heading word is rejected — and a leading NEW decree's provision bodies are
        excluded — by anchoring the returned region at the FIRST **amendment** decree johtolause
        (:data:`_DECREE_ENACT_RE`: the "Valtioneuvoston/…ministeriön päätöksen mukaisesti" +
        amend-verb signature that a law bill / rinnakkaisteksti can never carry).  No such
        amendment directive ⇒ nothing to recover ⇒ "" (unchanged behaviour).
      * The block ends at the next appendix heading after it (a decree's own rinnakkaisteksti,
        were one ever printed), else the document end.

    Because the appended block BEGINS at the decree amendment formula, only genuine draft-decree
    directives — never perustelut (which precede the law-bills) or the rinnakkaistekstit
    (which lie between ``lo`` and this block) — enter the scan.
    """
    d = flat.find(_ASETUSLUONNOS_HEADING, lo)
    if d < 0:
        return ""
    # lawvm-regex: witness_only PDF-witness structural anchor (draft-decree johtolause); never reads XML.
    enact = _DECREE_ENACT_RE.search(flat, d)
    if enact is None:
        return ""
    # lawvm-regex: witness_only bound the decree block before any trailing appendix reprint of it.
    end = _LAKIEHDOTUS_END_RE.search(flat, enact.start())
    return flat[enact.start() : end.start()] if end else flat[enact.start() :]


def _lakiehdotus_region(flat: str, *, aggressive: bool = False) -> str:
    """Flattened reading text with the appendix elided but the draft decrees kept.

    The bill directives (lakiehdotus) always precede the parallel-texts appendix, so cutting at
    the first appendix heading drops the spurious enacting-clause spans the two-column reprint
    would otherwise yield — without touching a genuine directive.  No heading present (the
    common case: HEs with no rinnakkaistekstit) → unchanged.

    A multi-instrument HE, however, prints its DRAFT-DECREE (asetusluonnos) proposals AFTER that
    appendix; they carry the same amendment grammar the XML witness parses, so eliding them with
    the appendix stranded their ops as op_missing.  When such a block is present
    (:func:`_asetusluonnos_region`) it is re-appended after the pre-appendix text (joined by a
    single space, which cannot bridge an enacting clause across the seam because the pre-appendix
    text ends at a completed bill).  HEs with no draft decrees are byte-for-byte unchanged.

    The appendix heading is resolved by :func:`_first_appendix_end`, which SKIPS a front-matter
    table-of-contents entry (an old-format HE lists "Rinnakkaistekstit … 41" in its
    sisällysluettelo; cutting there truncated the whole bill body to the cover page). A HE whose
    only appendix match is a TOC entry is left uncut.
    """
    m = _first_appendix_end(flat, aggressive=aggressive)
    if m is None:
        return flat
    head = flat[: m.start()]
    decree = _asetusluonnos_region(flat, m.end())
    return f"{head} {decree}" if decree else head


def _numbered_bill_follows(flat: str, p: int) -> bool:
    """True iff a numbered bill token ("<digits>. <bill-title>") begins at/just after ``flat[p:]``.

    The genuine "Lakiehdotukset" section heading is ALWAYS immediately followed by its first
    numbered bill; a stray capitalized "Lakiehdotukset" word in prose is not. Confirming the
    numbered-bill follow lets the heading anchor reject that stray word without a regex
    (bounded manual scan → the regex census stays flat).

    A numbered bill's title is either an AMEND-bill "Laki … / Laiksi … / Laeiksi …" (the
    common case) or a NEW-law bill whose title is a single capitalized COMPOUND word ENDING
    in "laki"/"laiksi" ("Lakiehdotukset 1. Yleistukilaki …", "… 1. Ampuma-aselaki …").
    Requiring only the "Laki …" head DROPPED the anchor for a compound-titled new-law bill,
    leaving the narrow char bound in force — which then silently drops that HE's long
    chapter-organized johtolause past the bound (its whole op-set lost as op_missing). The
    window is widened to 48 chars so the full first title word is seen for the ending test.
    """
    seg = flat[p : p + 48].lstrip(" ")
    i = 0
    while i < len(seg) and seg[i].isdigit():
        i += 1
    if i == 0:  # no bill ordinal
        return False
    rest = seg[i:].lstrip(" ")
    if not rest.startswith("."):
        return False
    title = rest[1:].lstrip(" ")
    if title.startswith(("Laki", "Laiksi", "Laeiksi")):
        return True
    first = title.split(" ", 1)[0].rstrip(".,:;").lower()
    return bool(first) and first.endswith(("laki", "laiksi"))


def _lakiehdotus_scan_start(flat: str) -> int:
    """Offset of the genuine "Lakiehdotukset" bills-section heading in ``flat``, else 0.

    A modern multi-bill HE prints its bill directives under a "Lakiehdotukset 1. Laki …"
    (nominative-plural) section heading. The perustelut that PRECEDE it discuss the same
    provisions with the same amendment-verb + citation + "§" + "seuraavasti" signature, so a
    widened char bound over the whole region would mis-read them as enacting clauses (op_extra).
    Anchoring the scan at this heading FENCES the perustelut out, which is what lets
    :func:`extract_enacting_clause_spans` widen the bound (:data:`_LAKIEHDOTUS_SCAN_BOUND`) to
    admit a mega-johtolause without that regression. An EARLIER "Lakiehdotukset" is the
    table-of-contents entry, so the LAST heading is taken (``str.rfind`` back-scan, no regex);
    each candidate must be followed by a numbered bill ("N. Laki") so a stray capitalized word
    is skipped. No heading (single-bill / older HEs) → 0: the scan opens at the region start and
    :func:`extract_enacting_clause_spans` keeps the unchanged narrow default bound.
    """
    end = len(flat)
    probe = len(_LAKIEHDOTUS_HEADING)
    while True:
        pos = flat.rfind(_LAKIEHDOTUS_HEADING, 0, end)
        if pos < 0:
            return 0
        if _numbered_bill_follows(flat, pos + probe):
            return pos
        end = pos  # skip this stray occurrence, keep back-scanning for the real heading


def _next_bill_head_pos(flat: str, lo: int, hi: int) -> int:
    """Start offset of the next amendment-verb head+citation in ``flat[lo:hi]``, else -1.

    An amendment-verb head (:data:`_HE_HEAD_VERB_RE`) followed by a parenthesized statute
    citation ``(NUM/YEAR)`` (:data:`_CITE_RE`) is the candidate signature of a NEW enacting
    directive — but it is only a CANDIDATE: a same-bill continuation verb
    ("kumotaan (id) N §, muutetaan 3 §:n 6 kohta ... sellaisina kuin ... ja (668/2013) ...")
    also matches, because a provenance clause's LAST enumerated law is sometimes
    parenthesized. The caller (:func:`_resolve_span_end`) disambiguates on a sentence-ending
    period, so this only has to locate the nearest candidate head.
    """
    for h in _HE_HEAD_VERB_RE.finditer(flat, lo, hi):
        cite = _CITE_RE.search(flat, h.end(), min(hi, h.end() + _HEAD_TO_CITE))
        if cite is not None:
            return h.start()
    return -1


def _resolve_span_end(flat: str, cite_end: int, term: "re.Match[str]") -> int:
    """Resolve a directive span's end, guarding a terminator-less repeal from a foreign one.

    Normally a directive ends at its own "... seuraavasti:" terminator (``term.end()``).
    A **terminator-less repeal** ("kumotaan <laki> (329/1999)." / "kumotaan (id) 5 §.") owns
    no "seuraavasti:", so its nearest FORWARD terminator belongs to whatever bill comes next
    — binding that bill's whole provision list to the repealed statute (the dominant op_extra
    source). We detect this by the SENTENCE BOUNDARY: a genuine repeal ends in a PERIOD, and
    the next bill's head+citation lies AFTER that period. So if a candidate later-bill head
    (:func:`_next_bill_head_pos`) is separated from this citation by a sentence-ending period,
    the terminator is FOREIGN and we re-bound the span to that period (a whole-law repeal then
    carries no "§" and is dropped by the caller's provision guard; a single-§ repeal keeps its
    "§" and lowers to its genuine repeal op). If NO period separates them, the "later head" is
    really a SAME-BILL continuation verb ("kumotaan (id) N §, muutetaan ... seuraavasti:")
    whose provenance clause merely happens to carry a parenthesized citation — so the full
    span to "seuraavasti:" is kept, and the combined bill's ops are preserved.

    A geom text-layer can GARBLE the next bill's amendment-verb head ("muutetaan" → the split
    "m uutetacm" seen in HE 114/1998), hiding the boundary from :func:`_next_bill_head_pos` so a
    consequential repeal in a voimaantulo clause ("Tällä lailla kumotaan (264/1961) 17 luvun 10
    §.") grabbed the NEXT bill's "seuraavasti:" and swept that bill's whole provision list onto
    the repealed statute (4 phantom ops on 264/1961 mis-cloned from the 547/1994 bill). The
    enactment FORMULA (:data:`_ENACTMENT_FORMULA`) that introduces EVERY johtolause is a second,
    garble-independent boundary signal (it survives when the verb does not, and it can never sit
    between this citation and its OWN terminator — it precedes the head verb), so we take the
    EARLIER of the verb-head boundary and the formula boundary.
    """
    nxt = _next_bill_head_pos(flat, cite_end, term.start())
    # ``str.find`` (not a regex) locates the enactment formula that opens the next bill's
    # johtolause — a boundary the verb-head detector misses when geom garbles the verb.
    formula = flat.find(_ENACTMENT_FORMULA, cite_end, term.start())
    candidates = [p for p in (nxt, formula) if p >= 0]
    if not candidates:
        return term.end()
    boundary = min(candidates)
    # A plain ``str.find`` (not a regex) locates the sentence-ending period between this
    # citation and the boundary — keeping the raw-``re.compile`` census flat.
    period = flat.find(".", cite_end, boundary)
    return period + 1 if period >= 0 else term.end()


def extract_enacting_clause_spans(
    reading_text: str, *, max_clause_chars: int = _MAX_CLAUSE_CHARS, aggressive: bool = False
) -> list[str]:
    """Segment PDF reading text into enacting-clause spans (named recognizer).

    ``aggressive`` (OFF by default; set only on the :func:`compare_he` no-clause FALLBACK)
    enables the reading-fidelity RECOVERIES that rescue an otherwise-undetectable clause without
    perturbing the working corpus: the slash-as-"1" cite repair + preceding-TOC-leader appendix
    uncut (both via ``_flatten_reading_text`` / ``_lakiehdotus_region``), and admitting a
    WHOLE-ANNEX / WHOLE-CHAPTER target (:data:`_ANNEX_CHAPTER_TARGET_RE`) as the provision
    marker for a "§"-less directive ("… lain (N/YEAR) liite … seuraavasti:").

    An enacting clause has a reliable SIGNATURE that survives geom line-reordering: a
    strong amendment-verb head ("muutetaan"/"lisätään"/"kumotaan"/"korvataan")
    immediately followed by a statute citation "(NUM/YEAR)", then at least one provision
    marker ("§"), running forward to a "... seuraavasti:" terminator.  We anchor on that
    signature rather than on the enactment formula's position — geom can scatter the
    centered "... päätöksen mukaisesti" formula arbitrarily far from its clause, so gating
    on it drops genuine clauses; the co-located "§" provision marker is the robust
    discriminator against a stray perustelut sentence.  A bare body-prose "säädetään" has
    no adjacent citation and is rejected.  Each qualifying span runs from the head to its
    nearest following terminator (bounded by ``max_clause_chars``).  Overlapping /
    duplicate spans (a second head before the same terminator; the rinnakkaistekstit
    repeat) are harmless — the op-set diff de-duplicates by target.  Returns spans in
    reading order.

    When the reading text carries the genuine "Lakiehdotukset N. Laki" bills-section heading
    (:func:`_lakiehdotus_scan_start`), the scan is ANCHORED there — the detailed-perustelut
    that precede it (which carry the same amendment-verb + citation + "§" + "seuraavasti"
    signature) are fenced out — and the head→terminator bound is WIDENED to
    :data:`_LAKIEHDOTUS_SCAN_BOUND` so a STRUCTURAL mega-johtolause (thousands of chars
    enumerating hundreds of provisions) is captured whole rather than dropped past the narrow
    default (the dominant mega-omnibus op_missing cause). With no bills heading (single-bill /
    older HEs) the scan opens at the region start with the unchanged narrow ``max_clause_chars``
    default, so those HEs — and the op_extra guard — are untouched.
    """
    flat = _lakiehdotus_region(
        _flatten_reading_text(reading_text, aggressive=aggressive), aggressive=aggressive
    )
    scan_lo = _lakiehdotus_scan_start(flat)
    # Anchored at the genuine bills heading the perustelut are fenced out, so the char bound
    # can be widened to admit a mega-johtolause; otherwise keep the narrow default.
    bound = _LAKIEHDOTUS_SCAN_BOUND if scan_lo else max_clause_chars
    spans: list[str] = []
    for head in _HE_HEAD_VERB_RE.finditer(flat, scan_lo):
        hstart = head.start()
        # The head must be followed by a statute citation "(NUM/YEAR)" within the window — the
        # existence anchor that separates a genuine johtolause from a stray perustelut sentence.
        cite = _CITE_RE.search(flat, head.end(), min(len(flat), head.end() + _HEAD_TO_CITE))
        if cite is None:
            continue
        # DROP a redundant CONTINUATION head whose only nearby "(NUM/YEAR)" is a history-list
        # amending act.  A combined johtolause ("kumotaan <name> (301/2004) N §, … muutetaan 3 §
        # … sellaisina kuin niistä ovat … ja (668/2013) …, seuraavasti:") is split by finditer
        # into one span per verb head; the CONTINUATION heads (muutetaan/lisätään) carry no
        # governing cite of their own — only the trailing history id — so they would lower the
        # SAME provisions the opening head's span already covers, but mis-attributed to the most
        # recent amending act (HE 139/2013: 26 phantom ops on 668/2013). We drop such a head only
        # when (a) it has NO governing cite (only history ids follow) AND (b) it is a continuation
        # (an earlier amendment-verb head shares its johtolause — no "seuraavasti:" between them).
        # An OPENING head whose sole cite is a history id is KEPT (existence anchor): a law named
        # by DATE, not a "(NUM/YEAR)" id ("kumotaan 3 päivänä joulukuuta 1895 annetun ulosottolain
        # … sellaisina kuin ne ovat … (389/73) …"), has no governing "(NUM/YEAR)" at all, and its
        # ops resolve to an EMPTY statute id on BOTH witnesses identically (both skip the history
        # id via _extract_statute_citation) so they still match — dropping it would strand them.
        if (
            _governing_cite_after(flat, head.end(), min(len(flat), head.end() + _HEAD_TO_CITE))
            is None
            and _is_continuation_head(flat, hstart, scan_lo)
        ):
            continue
        # The ambiguous "korvataan" head ("is reimbursed" in prose vs "is replaced" as a
        # directive) is admitted ONLY when the enactment formula corroborates it (else a body
        # cross-reference "… kustannukset korvataan (N/YEAR) …" lowers phantom ops on the
        # merely-referenced statute). The unambiguous heads need no corroboration.
        if head.group()[:4].lower() == "korv" and not _korvataan_head_is_directive(
            flat, hstart, cite.end()
        ):
            continue
        term = _TERMINATOR_RE.search(flat, hstart, hstart + bound)
        if term is None:
            continue
        # A terminator-less repeal ("kumotaan (id).") owns no "seuraavasti:"; if the nearest
        # one belongs to a LATER bill, re-bound the span to this directive's own sentence so
        # that bill's provision list is not mis-attributed to the repealed statute.
        end = _resolve_span_end(flat, cite.end(), term)
        # A genuine amendment directive lists provisions ("§") it touches; a stray
        # perustelut sentence with an amendment verb + citation does not. (A whole-law repeal
        # names no "§" and is dropped here; a single-§ repeal keeps its "§".) A WHOLE-ANNEX /
        # WHOLE-CHAPTER amendment ("… lain (N/YEAR) liite … seuraavasti:", "… uusi 4 a luku
        # seuraavasti:") lists no "§" yet is a genuine directive, so a nominative liite/luku
        # target (:data:`_ANNEX_CHAPTER_TARGET_RE`) is accepted as an equivalent marker — but
        # only on the no-clause FALLBACK (``aggressive``), so a §-listing corpus is untouched.
        # lawvm-regex: witness_only PDF-witness structural anchor (annex/chapter target); never reads XML.
        if _PROVISION_MARK_RE.search(flat, cite.end(), end) is None and not (
            aggressive and _ANNEX_CHAPTER_TARGET_RE.search(flat, cite.end(), end) is not None
        ):
            continue
        # No (hstart, end) dedup needed: finditer yields non-overlapping heads, so each span's
        # start is strictly increasing and unique (any two heads sharing a terminator still get
        # distinct starts; harmless same-target repeats de-dup downstream in the op-set diff).
        spans.append(flat[hstart:end])
    return spans


#: The candidate window handed to the LLM johtolause classifier — head + this many chars, enough
#: to see whether the candidate ENUMERATES provisions toward "seuraavasti:" (genuine) or reads as
#: explanatory perustelut prose. Must match what the tag cache keys on, so it is a fixed constant.
_LLM_CLASSIFY_WINDOW = 500

#: Safety cap on the head→"seuraavasti:" search for the LLM lane. The LLM gate (not a char count)
#: is the precision discriminator, so this is only a runaway guard: it is set well ABOVE the
#: largest real johtolause (~13.4k over the census) so a genuine mega-amendment is never truncated,
#: yet finite so a head that genuinely has NO terminator cannot grab an arbitrarily distant one.
_MAX_LLM_CLAUSE_CHARS = 60000


def extract_enacting_clause_spans_llm(
    reading_text: str,
    *,
    classify_fn: "Callable[[str], object]",
    max_clause_chars: int = _MAX_LLM_CLAUSE_CHARS,
    aggressive: bool = False,
) -> list[str]:
    """LLM-gated enacting-clause extraction: mechanical candidates, LLM johtolause gate, UNBOUNDED span.

    The mechanical :func:`extract_enacting_clause_spans` has no clean bound: small drops mega-bill
    johtolauses (~13k chars → whole-bill op_missing, 82% of op_missing over the 8435-HE census),
    large turns perustelut prose into false clauses (op_extra explosion). This variant removes the
    length decision. It enumerates the SAME candidate heads (amendment verb + statute citation +
    "§" before a "seuraavasti:") but replaces the tight char bound with an LLM CLASSIFICATION:
    ``classify_fn(window)`` returns a :class:`~lawvm.finland.he_johtolause_tagger.JohtolauseTag`
    (real use: the cache-through ``classify_candidate_cached`` bound to a store + local-LLM
    ``chat_fn``); only a genuine ``JOHTOLAUSE`` candidate is kept, and its span runs UNBOUNDED to
    its own terminator (up to a generous runaway cap). ``classify_fn`` is injected so the whole
    lane is hermetically testable with a scripted classifier — and the LLM only SEGMENTS here; the
    spans still flow through the deterministic ``_parse_one_clause`` and the ops are still
    EXACT-compared against the trusted XML, so the exactness invariant is untouched.
    """
    from lawvm.finland.he_johtolause_tagger import JohtolauseTag

    flat = _lakiehdotus_region(
        _flatten_reading_text(reading_text, aggressive=aggressive), aggressive=aggressive
    )
    spans: list[str] = []
    for head in _HE_HEAD_VERB_RE.finditer(flat):
        hstart = head.start()
        cite = _CITE_RE.search(flat, head.end(), min(len(flat), head.end() + _HEAD_TO_CITE))
        if cite is None:
            continue
        # Same ambiguous-"korvataan" structural gate as the mechanical lane: a body cross-
        # reference "… kustannukset korvataan (N/YEAR) …" must not be lowered as a phantom clause.
        if head.group()[:4].lower() == "korv" and not _korvataan_head_is_directive(
            flat, hstart, cite.end()
        ):
            continue
        # LLM gate (cheap, cached): reject perustelut prose before locating a terminator.
        tag = classify_fn(flat[hstart : hstart + _LLM_CLASSIFY_WINDOW])
        if tag is not JohtolauseTag.JOHTOLAUSE:
            continue
        term = _TERMINATOR_RE.search(flat, hstart, hstart + max_clause_chars)
        if term is None:
            continue
        # Terminator-less repeal guard (see extract_enacting_clause_spans): a foreign later
        # bill's terminator is not claimed for this repeal — re-bound to its own sentence.
        end = _resolve_span_end(flat, cite.end(), term)
        # A §-less WHOLE-ANNEX / WHOLE-CHAPTER target is admitted only on the fallback, mirroring
        # the mechanical lane (:func:`extract_enacting_clause_spans`).
        # lawvm-regex: witness_only PDF-witness structural anchor (annex/chapter target); never reads XML.
        if _PROVISION_MARK_RE.search(flat, cite.end(), end) is None and not (
            aggressive and _ANNEX_CHAPTER_TARGET_RE.search(flat, cite.end(), end) is not None
        ):
            continue
        # No (hstart, end) dedup set needed: finditer yields non-overlapping heads, so hstart —
        # and thus each span's start — is strictly increasing and unique.
        spans.append(flat[hstart:end])
    return spans


# --------------------------------------------------------------------------- #
# Flattened op model + diff (matched on statute/provision target + kind).      #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class HEFlatOp:
    """One proposed op flattened to its comparable ``(kind, target)`` shape.

    ``target_ref`` is the ``BranchProposedOp.target_provision_ref`` (statute id +
    provision, e.g. ``577/2005/9/2``), the key the op-diff pairs the two witnesses on.
    ``action`` is the lowered ``operation_kind`` (replace / insert / repeal / ...).
    """

    action: str
    target_ref: str

    @property
    def render(self) -> str:
        return f"{self.action} {self.target_ref}"


def flatten_branch_ops(ops) -> tuple[HEFlatOp, ...]:
    """Flatten a ``BranchProposedOp`` iterable to comparable ``HEFlatOp``s.

    Ops with an empty target reference (no statute citation resolved) are dropped: a
    proposed op that names no structural target cannot be matched op-to-op (it surfaces
    on the trusted XML side as a target-resolution finding, not as a comparable op).
    """
    out: list[HEFlatOp] = []
    for op in ops:
        ref = getattr(op, "target_provision_ref", "") or ""
        if not ref or ref.strip("/") == "":
            continue
        out.append(HEFlatOp(action=str(op.operation_kind), target_ref=ref))
    return tuple(out)


def diff_proposed_ops(
    xml_ops: tuple[HEFlatOp, ...], pdf_ops: tuple[HEFlatOp, ...]
) -> tuple[OpDivergence, ...]:
    """Op-level diff of the XML (reference) proposed ops against the PDF (under-test) ops.

    Ops are matched by ``target_ref``; a matched pair with the same action is
    ``matched``, a different action is ``kind_mismatch``.  A target only on the XML side
    is ``op_missing_in_pdf``; only on the PDF side is ``op_extra_in_pdf``.  Ordering is
    deterministic (XML reading order first, then PDF-only ops).  First-wins on a
    duplicate target within one witness (rare; rinnakkaistekstit dupes collapse here).
    """

    def _index(flat: tuple[HEFlatOp, ...]) -> dict[str, HEFlatOp]:
        idx: dict[str, HEFlatOp] = {}
        for op in flat:
            idx.setdefault(op.target_ref, op)
        return idx

    pdf_idx = _index(pdf_ops)
    out: list[OpDivergence] = []
    seen: set[str] = set()

    for op in xml_ops:
        ref = op.target_ref
        if ref in seen:
            continue
        seen.add(ref)
        pdf_op = pdf_idx.get(ref)
        if pdf_op is None:
            out.append(
                OpDivergence(
                    kind="op_missing_in_pdf",
                    target_ref=ref,
                    xml_op=op.render,
                    pdf_op=None,
                    detail="proposed op present in XML IR, absent from PDF IR",
                )
            )
        elif pdf_op.action == op.action:
            out.append(
                OpDivergence(
                    kind="matched", target_ref=ref, xml_op=op.render, pdf_op=pdf_op.render, detail=""
                )
            )
        else:
            out.append(
                OpDivergence(
                    kind="kind_mismatch",
                    target_ref=ref,
                    xml_op=op.render,
                    pdf_op=pdf_op.render,
                    detail=f"same target, op kind differs: xml={op.action} pdf={pdf_op.action}",
                )
            )

    for op in pdf_ops:
        ref = op.target_ref
        if ref in seen:
            continue
        seen.add(ref)
        out.append(
            OpDivergence(
                kind="op_extra_in_pdf",
                target_ref=ref,
                xml_op=None,
                pdf_op=op.render,
                detail="proposed op present in PDF IR, absent from XML IR",
            )
        )

    return tuple(out)


# --------------------------------------------------------------------------- #
# Out-of-scope witness-disagreement reclassification (metric integrity).       #
# --------------------------------------------------------------------------- #
#
# An omnibus HE amends MANY statutes (often incl. decrees/asetukset the law-level
# trusted XML models only a SUBSET of). When the PDF reads proposed ops on a statute the
# XML op-set never names, the outcome is one of THREE things, and honest typing must tell
# them apart — a benign catch-all that force-labels all three "witness disagreement" hides
# a target-misresolution DEFECT (the phase-1 lesson, in reverse):
#
#   (a) a GENUINE SECOND BILL — a real bill TITLE ("Laki <act>:n muuttamisesta") governs
#       the block; the PDF out-read a narrow oracle and is MORE complete. First-class
#       witness disagreement → ``pdf_out_of_scope_statute``.
#   (b) a GENUINE CONSEQUENTIAL REPEAL — a commencement clause of ANOTHER bill repeals a
#       provision of an outside act ("Tällä lailla kumotaan … vesilain (264/1961) 17 luvun
#       10 §."); a real proposed effect the law-level XML omits, but NOT a second bill (no
#       title). First-class witness disagreement → ``pdf_consequential_repeal``.
#   (c) a PDF DEFECT — target MIS-ATTRIBUTION: the block has NEITHER a governing bill title
#       NOR a consequential-repeal head; it is phantom (a sibling bill's provisions cloned
#       onto the wrong statute by a span-boundary slip). Stays ``op_extra_in_pdf`` (defect).
#
# The gate is LABEL-INDEPENDENT and PDF-STRUCTURAL: it never reads the XML op-set to decide
# a type — only whether a title / consequential-repeal formula governs the citation in the
# lakiehdotus reading text. Statute-id present in the XML op-set (same-statute granularity)
# is not out-of-scope at all and STAYS ``op_extra_in_pdf``. The prior heuristic — "a
# CONTIGUOUS ≥3-op block on an XML-absent statute is a second bill" — was UNSOUND: a
# span-boundary slip (a consequential repeal grabbing a sibling bill's provision list, HE
# 114/1998) manufactures exactly such a "coherent block", and the size proxy force-benigned
# the phantom. Block size is no longer a criterion; the GOVERNING HEAD is.

#: A first-class witness-disagreement outcome (NOT a PDF defect): the PDF captured a whole
#: amendment block on a statute the trusted XML op-set omits, governed by a real bill TITLE
#: (omnibus-HE second bill).
_PDF_OUT_OF_SCOPE_STATUTE = "pdf_out_of_scope_statute"

#: A first-class witness-disagreement outcome (NOT a PDF defect): the PDF captured a
#: CONSEQUENTIAL REPEAL of an outside act's provision, embedded in another bill's
#: commencement/voimaantulo clause ("Tällä lailla kumotaan … (N/YEAR) … §."), which the
#: law-level XML op-set omits. A real proposed effect — PDF more complete — but NOT a
#: titled second bill, so it is typed distinctly rather than folded into either bucket.
_PDF_CONSEQUENTIAL_REPEAL = "pdf_consequential_repeal"

#: A bill-TITLE head word ("Laki …" / "Laiksi …" / "Laeiksi …"): the case-sensitive
#: NOMINATIVE heading word that opens every numbered bill. Kept as spaced string literals
#: (not a regex) — the same flat-census discipline as :data:`_ENACTMENT_FORMULA` /
#: :data:`_LAKIEHDOTUS_HEADING` (FW-07: no new semantic-plane raw ``re.compile``). Capital
#: "L" is load-bearing: a mid-sentence inflected "…tätä lakia…" or a compound "…rikoslaki…"
#: uses a lowercase "l", so it is not a bill title.
_BILL_TITLE_HEADS = (" Laki ", " Laiksi ", " Laeiksi ")

#: An amend-bill title's TAIL ("Laki <act>:n muuttamisesta" / "… kumoamisesta"). Matched by
#: ``str.find`` (no regex); the title HEAD word must sit within :data:`_TITLE_HEAD_REACH`
#: chars before it. This is the GENUINE-second-bill signature the benign
#: :data:`_PDF_OUT_OF_SCOPE_STATUTE` gate requires — a coherent op block alone (or a
#: consequential repeal) does NOT qualify.
_BILL_TITLE_TAILS = ("muuttamisesta", "kumoamisesta", "muutoksesta")

#: Max chars from a bill-title HEAD word to its amend TAIL (the amended act's name spans the
#: gap; budgeted wide for long EU-implementation act names).
_TITLE_HEAD_REACH = 240

#: Max distance from a bill TITLE's amend tail to the "(N/YEAR)" citation of the act it
#: amends (the title is followed by the enactment formula + verb head + long act name +
#: cite). Mirrors :data:`_HEAD_TO_CITE`'s budget with margin for the intervening formula.
_TITLE_TO_CITE = 500

#: The consequential-repeal formula that opens a commencement clause's repeal ("Tällä lailla
#: kumotaan …", lowercased for a case-insensitive ``str.find`` — the same discipline as
#: :data:`_ENACTMENT_FORMULA`). "By this act is repealed", categorically distinct from a
#: johtolause repeal head ("kumotaan <act> (N/YEAR) … seuraavasti:").
_CONSEQUENTIAL_REPEAL_MARK = "lailla kumotaan"

#: How far BEFORE an outside-act "(N/YEAR)" citation the consequential-repeal formula may sit
#: ("Tällä lailla kumotaan 19 päivänä toukokuuta 1961 annetun vesilain (264/1961) …" ≈ 60
#: chars). Bounded so an unrelated earlier repeal formula is not swept in.
_CONSEQUENTIAL_REPEAL_REACH = 160


def _statute_id_of(target_ref: str) -> str:
    """Reduce a ``target_provision_ref`` to its statute id ("1707/1995/9/2" → "1707/1995").

    A bare / malformed ref with fewer than two path parts yields "" (never a statute).
    Uses ``str.split`` (no regex) so the semantic-plane regex census stays flat.
    """
    parts = [p for p in target_ref.split("/") if p]
    if len(parts) < 2:
        return ""
    return f"{parts[0]}/{parts[1]}"


def _titled_bill_statute_ids(flat: str) -> set[str]:
    """Statute ids GOVERNED BY A REAL BILL TITLE in the lakiehdotus reading text.

    A genuine second bill is introduced by a nominative bill TITLE — a heading word
    (:data:`_BILL_TITLE_HEADS`) + the amended act's name + an amend TAIL
    (:data:`_BILL_TITLE_TAILS`), e.g. "Laki … annetun lain muuttamisesta". The amended act's
    ``(N/YEAR)`` citation sits in the johtolause that FOLLOWS the title (past the enactment
    formula + verb head); we map each title tail to the FIRST citation within
    :data:`_TITLE_TO_CITE` chars after it — that bill's own act — and return the resolved
    statute-id set. A citation that is NOT the first one after a title (a consequential repeal
    buried in a voimaantulo clause, a body cross-reference) never enters this set, so it
    cannot be force-benigned as a second bill. All matching is ``str.find`` on the flat
    reading text (no semantic-plane regex); purely PDF-structural, never reads the XML op-set.
    """
    ids: set[str] = set()
    # Pad so a title head word flush at offset 0 still carries its leading-space form.
    padded = " " + flat
    for tail in _BILL_TITLE_TAILS:
        pos = padded.find(tail)
        while pos >= 0:
            window = padded[max(0, pos - _TITLE_HEAD_REACH):pos]
            # Nearest title HEAD word before the tail, and — the discriminator against
            # perustelut prose (a stray "Laki …" and a later "…muuttamisesta" in separate
            # sentences) — NO sentence/clause break ('.'/':' ) between the head and the tail.
            hi = max(window.rfind(h) for h in _BILL_TITLE_HEADS)
            if hi >= 0 and "." not in window[hi:] and ":" not in window[hi:]:
                real = pos - 1  # un-pad back to a ``flat`` offset for the cite search
                # lawvm-regex: witness_only PDF-witness cite anchor after a bill title; never reads XML.
                cite = _CITE_RE.search(flat, real, min(len(flat), real + _TITLE_TO_CITE))
                if cite is not None:
                    ids.add(_statute_id_of_cite(cite.group()))
            pos = padded.find(tail, pos + 1)
    ids.discard("")
    return ids


def _statute_has_consequential_repeal(flat: str, sid: str) -> bool:
    """True iff ``flat`` repeals a provision of statute ``sid`` via a COMMENCEMENT clause.

    The signature is the consequential-repeal formula (:data:`_CONSEQUENTIAL_REPEAL_MARK`,
    "Tällä lailla kumotaan …") sitting within :data:`_CONSEQUENTIAL_REPEAL_REACH` chars
    BEFORE a ``(sid)`` citation — the "by this act is repealed <outside act> (N/YEAR) N §."
    pattern an omnibus HE uses to retire a provision of an act it does not otherwise amend.
    This is categorically distinct from a johtolause repeal head ("kumotaan <act> (N/YEAR) …
    seuraavasti:") — the formula names no ``seuraavasti:`` and sits in a voimaantulo clause.
    All matching is ``str.find`` (no regex); purely PDF-structural, never reads the XML.
    """
    needle = f"({sid})"
    low = flat.lower()
    pos = flat.find(needle)
    while pos >= 0:
        lo = max(0, pos - _CONSEQUENTIAL_REPEAL_REACH)
        if _CONSEQUENTIAL_REPEAL_MARK in low[lo:pos]:
            return True
        pos = flat.find(needle, pos + 1)
    return False


def _reclassify_out_of_scope_second_bills(
    divergences: tuple[OpDivergence, ...], xml_ops: tuple[HEFlatOp, ...], flat: str
) -> tuple[OpDivergence, ...]:
    """Retype XML-absent ``op_extra`` blocks by their GOVERNING HEAD (metric integrity).

    Scans the divergence stream (``op_extra`` divergences are emitted contiguously, in PDF
    reading order, at the tail of :func:`diff_proposed_ops`). A maximal CONTIGUOUS run of
    ``op_extra_in_pdf`` divergences sharing ONE statute-id that is ABSENT from the XML op-set
    is classified LABEL-INDEPENDENTLY by what governs that statute in ``flat`` (the
    lakiehdotus reading text):

    * a real bill TITLE (:func:`_titled_bill_statute_ids`) → :data:`_PDF_OUT_OF_SCOPE_STATUTE`
      (genuine second bill, PDF more complete);
    * a consequential-repeal formula (:func:`_statute_has_consequential_repeal`) →
      :data:`_PDF_CONSEQUENTIAL_REPEAL` (a real effect the XML omits, but not a second bill);
    * NEITHER → left as ``op_extra_in_pdf`` (a phantom target-misresolution DEFECT — a block
      that "looks coherent" but has no governing head, e.g. a sibling bill's provisions
      cloned onto the wrong statute).

    Block SIZE is deliberately NOT a criterion (the prior ``≥3`` proxy force-benigned exactly
    the phantom block a span-boundary slip manufactures). Any op on a statute-id the XML
    op-set DOES name (same-statute granularity) is not out-of-scope and STAYS
    ``op_extra_in_pdf``. Only the ``kind``/``detail`` of reclassified rows change; ``matched``
    / ``op_missing_in_pdf`` / ``kind_mismatch`` / ``payload_mismatch`` rows are untouched.
    """
    xml_statute_ids = {_statute_id_of(op.target_ref) for op in xml_ops}
    xml_statute_ids.discard("")
    titled_ids = _titled_bill_statute_ids(flat)
    out = list(divergences)
    n = len(out)
    i = 0
    while i < n:
        d = out[i]
        sid = _statute_id_of(d.target_ref)
        if d.kind != "op_extra_in_pdf" or not sid or sid in xml_statute_ids:
            i += 1
            continue
        # Extend a contiguous run of op_extra on this same absent statute-id.
        j = i + 1
        while (
            j < n
            and out[j].kind == "op_extra_in_pdf"
            and _statute_id_of(out[j].target_ref) == sid
        ):
            j += 1
        block = j - i
        kind: Optional[str] = None
        detail = ""
        if sid in titled_ids:
            kind = _PDF_OUT_OF_SCOPE_STATUTE
            detail = (
                f"PDF captured a {block}-op amendment block on statute {sid}, GOVERNED BY A "
                "REAL BILL TITLE (Laki … muuttamisesta) yet ABSENT from the trusted XML op-set "
                "— the genuine second-bill signature of an omnibus HE whose XML models only a "
                "subset of amended statutes; first-class witness disagreement (PDF more "
                "complete), NOT a PDF op_extra defect"
            )
        elif _statute_has_consequential_repeal(flat, sid):
            kind = _PDF_CONSEQUENTIAL_REPEAL
            detail = (
                f"PDF captured a {block}-op consequential repeal on statute {sid} via a "
                "commencement clause (Tällä lailla kumotaan … (N/YEAR) … §.) of another bill "
                "— a real proposed effect the law-level XML op-set omits; first-class witness "
                "disagreement (PDF more complete), NOT a titled second bill and NOT a defect"
            )
        if kind is not None:
            for k in range(i, j):
                out[k] = replace(out[k], kind=kind, detail=detail)
        i = j
    return tuple(out)


# --------------------------------------------------------------------------- #
# Payload stage — proposed body-text equivalence for MATCHED ops.              #
# --------------------------------------------------------------------------- #
#
# The op-structure diff proves both witnesses name the SAME proposed provision + verb.
# The payload stage proves the PROPOSED BODY TEXT they carry for that provision is the
# same too, modulo ``op_equivalence.text_equivalence``'s inert quotient.  The proposed
# text lives in the bill body (XML: the statuteProvisionsWrapper <section> bodies; PDF:
# the bill text after "... seuraavasti:").  Both are keyed by (statute-id, section label) —
# the op's BILL scope — so an omnibus HE's cross-bill "N §" reuse never pairs bill A's body
# against bill B's op; a key absent on either witness is TYPE-DEFERRED — counted, never
# forced into a spurious payload_mismatch.  REPEAL / commencement ops carry no body.

#: Section-body header inside a bill's PDF reading text ("7 §", "2 a §"); the
#: ``(?!\s{0,2}:)`` guard rejects a case-inflected cross-reference ("4 §:n 1 kohta").
_PDF_SECTION_HEADER_RE = re.compile(r"(\d{1,4}\s{0,3}[a-zä]?)\s{0,3}§(?!\s{0,2}:)", re.IGNORECASE)

#: Leading "N §" address header stripped from a payload so the comparison is over prose.
_LEADING_SECTION_HEADER_RE = re.compile(r"^\s{0,4}\d{1,4}\s{0,3}[a-zä]?\s{0,3}§\s*", re.IGNORECASE)

#: A section body's trailer boundary: a bill/appendix/rinnakkaistekstit heading or the ENACTING
#: FURNITURE that follows a bill's last provision.  The last section of a bill otherwise runs to
#: the next "N §" (or EOF) and swallows the parallel-texts / appendix / signature block that the
#: trusted XML section body never carries — a spurious payload_mismatch.  We bound each PDF
#: section body at the FIRST such marker: the signature block ("Tasavallan Presidentti <NAME>",
#: case-sensitive "Presidentti" so a body's own "…tasavallan presidentin asetuksella" is
#: untouched), the next-law title heading ("2. Laki …", case-sensitive "Laki" so a mid-sentence
#: "…2. laki…" is untouched), and a chapter heading ("10 luku …", nominative "luku" only so an
#: inflected "…5 luvun…" cross-reference is untouched).  The commencement clause is handled
#: separately by :data:`_PDF_BODY_VOIMAANTULO_RE` (a genuine voimaantulo §-body STARTS with it).
#: Flat/bounded quantifiers; case scoped with ``(?-i:…)`` (FW-07).
#:
#: A dash-run divider ("———"/"— — — —") is NOT a terminator here: it is split out into
#: :data:`_PDF_BODY_DIVIDER_RE` and disambiguated per-run by :func:`_pdf_divider_is_omission`,
#: because a run has two roles — a MID-body OMISSION divider (the XML elides it and KEEPS the
#: text on both sides) vs an END divider (the XML drops everything after it).  Folding the run
#: in with the genuine terminators truncated a role-(a) omission body at its FIRST dash.
_PDF_BODY_TRAILER_RE = re.compile(
    r"(?:\bRinnakkaistekstit\b"
    r"|\bLiitteet?\b"
    r"|\bVoimassa\s+oleva\s+laki\b"
    r"|\bEhdotus\b"
    r"|(?-i:Tasavallan\s+Presidentti)"
    r"|(?-i:\b\d{1,2}\.\s{0,3}Laki\b)"
    r"|\b\d{1,3}\s{0,3}luku\b)",
    re.IGNORECASE,
)

#: An entry-into-force / omission divider run.  The first alternative matches a CONTIGUOUS run
#: ("———"/"—————"); the second the SPACED run ("— — — —") the text layer emits when a centered
#: divider's glyphs are laid out with intervening spaces.  Requiring at least THREE dashes is
#: conservative: a single in-sentence em-dash ("sana — toinen") or a two-dash pair never fires —
#: only a genuine divider run does.  Flat/bounded quantifiers (FW-07).
_PDF_BODY_DIVIDER_RE = re.compile(r"(?:[—–\-]{3,}|[—–\-](?:\s[—–\-]){2,40})")

#: Collapse a whitespace run to a single space when re-joining elided body segments.
_WS_RUN_RE = re.compile(r"\s+")

#: Per-divider role classifier support.  An OMISSION divider is followed by RETAINED provision
#: text (a "N)" kohta marker or a prose sentence); an END divider is followed by the commencement
#: clause, only page furniture, or nothing.  Flat/bounded quantifiers (FW-07).
_PDF_DIVIDER_LEAD_RE = re.compile(r"^[—–\-\s]+")
_PDF_DIVIDER_PAGENUM_RE = re.compile(r"^\d{1,3}(?:\s|$)")
_PDF_COMMENCEMENT_HEAD_RE = re.compile(
    r"Tämä\s+(?:laki|asetus)\s+tulee\s+voimaan", re.IGNORECASE
)
_PDF_KOHTA_MARKER_RE = re.compile(r"\d{1,2}\s{0,2}\)")


def _pdf_divider_is_omission(after: str) -> bool:
    """True iff a dash-run is a MID-body OMISSION divider (elide + keep reading), else END (trim).

    Decided PURELY from the PDF text that FOLLOWS the run up to the next genuine terminator — the
    XML body / answer key is NEVER consulted, so the rule holds on novel PDFs (phase 5).  A
    POSITIVE continuation signal (a "N)" kohta marker or a retained prose sentence) => omission;
    the commencement clause ("Tämä laki tulee voimaan …"), only page furniture, or nothing => END.
    Precision-first / ASYMMETRIC risk: a false ELIDE over-captures the next section (a regression),
    a false TRIM only misses a recovery — so the omission verdict requires the positive signal.
    """
    s = _PDF_DIVIDER_LEAD_RE.sub("", after)  # skip any further dash glyphs / spaces
    m = _PDF_DIVIDER_PAGENUM_RE.match(s)  # skip a lone page number (never a "N)" kohta marker)
    if m:
        s = _PDF_DIVIDER_LEAD_RE.sub("", s[m.end():])
    if not s:
        return False  # nothing retained before the terminator -> END divider
    if _PDF_COMMENCEMENT_HEAD_RE.match(s):
        return False  # commencement clause follows -> END divider
    if _PDF_KOHTA_MARKER_RE.match(s):
        return True  # a retained "N)" kohta -> OMISSION divider
    return s[0].isalpha()  # a retained prose sentence -> OMISSION divider

#: The commencement clause "Tämä laki tulee voimaan …" appended after a bill's last
#: substantive provision (the XML keeps it as a SEPARATE unnumbered section, so the PDF's last
#: numbered §-body over-captures it → a spurious payload_mismatch).  It is trimmed ONLY when
#: it follows a sentence-ending period (the substantive provision's own final "."), via the
#: fixed-width look-behind: a genuine voimaantulo §-body (XML §5 = "Tämä laki tulee voimaan
#: …") is NOT preceded by an in-body period and is left whole, so this never truncates a real
#: commencement provision.  The DECREE form ("Tämä asetus tulee voimaan …") is accepted too, so
#: a recovered draft-decree §-body (see :func:`_asetusluonnos_region`) sheds its commencement
#: tail exactly as a law body does; the alternative only reaches decree bodies (a law body never
#: contains "Tämä asetus tulee voimaan").  Flat/bounded quantifiers (FW-07).
_PDF_BODY_VOIMAANTULO_RE = re.compile(
    r"(?<=\.)\s{0,3}Tämä\s+(?:laki|asetus)\s+tulee\s+voimaan", re.IGNORECASE
)

#: A lone page number the text layer appends at the very END of a body ("…tulosta. 40"). It is
#: stripped only when it is BOTH end-anchored AND preceded by the provision's own sentence-
#: ending period — a genuine provision does not end "<sentence>. <bare integer>", whereas a
#: real trailing figure sits BEFORE its period ("…enintään 40."), so the period+end anchoring
#: leaves genuine content untouched.  Fixed-width look-behind, flat quantifiers (FW-07).
_PDF_BODY_TRAILING_PAGENUM_RE = re.compile(r"(?<=\.)\s{1,3}\d{1,3}\s{0,3}$")

#: A section's TITLE heading (otsikko) sits in reading order just BEFORE that section's "N §"
#: number, so when a body runs up to the FOLLOWING "N §" header it over-captures the next
#: section's title ("…1 luvun 1 §:ssä. Poliisimiehen virka-asemaan liittyvät säännökset" —
#: the trailing phrase is the NEXT section's heading; "…virkamieslaissa. Erinäiset säännökset").
#: The current boundary stops at the next "N §" NUMBER, but the title precedes that number, so
#: the boundary is too LATE. The XML section body never carries the next title, so it is a
#: spurious payload_mismatch. A section otsikko is a SHORT, Capitalized noun phrase (often
#: ending "…säännökset", "Voimaantulo", "Määritelmät", "Soveltamisala") that — unlike genuine
#: body prose — carries NO sentence-ending period; we trim it off the body's tail, keeping the
#: body's own final sentence (through its last period) INTACT. Below are the precision bounds:
#: only a short, few-word, punctuation-free, digit-free Capitalized trailing phrase is treated
#: as a title. Anything else leaves the body slightly under-trimmed (a typed divergence) rather
#: than risk CUTTING real content — a title is short/Capitalized/§-adjacent, body prose is not.
_SECTION_TITLE_MAX_CHARS = 64
_SECTION_TITLE_MAX_WORDS = 8


def _looks_like_section_title(tail: str) -> bool:
    """True iff ``tail`` is a short Capitalized section-title heading (no sentence period).

    A section otsikko is a brief Capitalized noun phrase ("Erinäiset säännökset",
    "Voimaantulo", "Määritelmät") carrying NO sentence-ending period, ":" list-intro, ";",
    "§" marker, or digit — the discriminators that separate it from a genuine trailing body
    SENTENCE (which ends in a period) or a provision cross-reference. Pure bounded ``str``
    scan (no regex) so the semantic-plane regex census stays flat (the same discipline as
    :func:`_statute_id_of` / :func:`_resolve_span_end`). Precision-first: a phrase failing
    ANY test is NOT trimmed, leaving the body under-trimmed rather than cutting real content.
    """
    if not tail or len(tail) > _SECTION_TITLE_MAX_CHARS:
        return False
    if not tail[0].isupper():
        return False
    for ch in tail:
        if ch in ".:;§" or ch.isdigit():
            return False
    return len(tail.split()) <= _SECTION_TITLE_MAX_WORDS


#: Payload-body containers whose <section> children are proposed statute text.
_PAYLOAD_WRAPPER_NAME = "statuteProvisionsWrapper"

_PAYLOAD_CANON_TRIM = 80


def _section_label_of(target_ref: str) -> str:
    """Reduce a ``target_provision_ref`` to its section label ("577/2005/9/2" → "9").

    Sub-section / item ops share their section's proposed body, so the momentti / kohta
    tail is dropped; a bare statute-level ref ("577/2005") yields "".
    """
    parts = [p for p in target_ref.split("/") if p]
    # parts: [num, year, section, momentti?, kohta?] — the section is index 2.
    if len(parts) < 3:
        return ""
    sec = parts[2]
    if sec.startswith("luku_"):
        return ""
    return sec


def _normalize_section_label(raw: str) -> str:
    """Canonicalize a section label token ("12 a" → "12a", "7" → "7")."""
    return re.sub(r"\s+", "", raw or "").lower()


def _governing_amendment_statute_id(text: str) -> str:
    """Statute id AMENDED by the enacting clause in ``text`` ("609/1986"), else "".

    The amended act is the citation "(N/YEAR)" governed by an amendment-verb head
    ("muutetaan … lain (609/1986) 17 § … seuraavasti:") — NOT merely the first citation in
    the text, because a NEW-law bill ("… säädetään:") carries no amended act yet its section
    bodies cross-reference other laws ("… yhdenvertaisuuslain ja tasa-arvolain (609/1986)"),
    whose citation would otherwise mis-scope that new law's sections onto an unrelated statute.
    We take the FIRST amendment-head-governed citation followed by a "§" provision marker — the
    same gate :func:`_enacting_clause_regions` uses on the PDF side, so both witnesses resolve a
    bill's scope identically. A johtolause precedes its section bodies in document order, so this
    first gated head+cite is the genuine amended act; a new-law bill (no amendment head) yields "".
    """
    for h in _HE_HEAD_VERB_RE.finditer(text):
        cite = _CITE_RE.search(text, h.end(), min(len(text), h.end() + _HEAD_TO_CITE))
        if cite is None:
            continue
        if _PROVISION_MARK_RE.search(text, cite.end(), min(len(text), cite.end() + _MAX_CLAUSE_CHARS)):
            return _statute_id_of_cite(cite.group())
    return ""


def _wrapper_statute_id(wrapper) -> str:
    """Governing statute id of a ``statuteProvisionsWrapper``'s bill ("609/1986"), else "".

    A wrapper sits inside its bill's ``hcontainer name="bill"`` (or, defensively, its direct
    parent). Its bill scope is the act AMENDED by the bill's enacting clause
    (:func:`_governing_amendment_statute_id`) — the same governing-citation the parser resolves
    each op's ``target_statute_id`` from. Scoping the section bodies to this id keeps an omnibus
    HE's cross-bill "N §" reuse from first-wins collapsing (bill A "17 §" vs bill B "17 §"). A
    new-law bill / unresolvable scope → "" (the op payload is then deferred, precision-first,
    never paired to a wrong bill's body).
    """
    node = wrapper
    while node is not None and node.attrib.get("name") != "bill":
        node = node.getparent()
    scope = node if node is not None else wrapper.getparent()
    if scope is None:
        return ""
    return _governing_amendment_statute_id(_element_text(scope))


def _xml_proposed_bodies(xml_bytes: bytes) -> dict[tuple[str, str], str]:
    """Map (statute-id, section label) → proposed body text from the HE bill wrappers.

    Each ``statuteProvisionsWrapper``'s section bodies are scoped to their bill's governing
    statute id (:func:`_wrapper_statute_id`) so an omnibus HE that reuses a section number
    across bills keeps the two bodies DISTINCT — a bare-label key first-wins-collapsed them,
    so an op matched to bill A's ``.../17`` was compared against bill B's ``17 §`` body. A
    wrapper whose bill scope is unresolvable ("") is skipped (its ops defer, precision-first).
    First-wins within a single ``(statute, label)`` scope. Legacy enactment sections indexed.
    """
    from lxml import etree

    try:
        root = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError:
        return {}
    out: dict[tuple[str, str], str] = {}
    for el in root.iter():
        if el.attrib.get("name") != _PAYLOAD_WRAPPER_NAME:
            continue
        sid = _wrapper_statute_id(el)
        if not sid:
            continue
        for sec in el:
            tag = sec.tag
            lname = tag.rsplit("}", 1)[-1] if isinstance(tag, str) and "}" in tag else tag
            if lname != "section":
                continue
            text = _element_text(sec)
            hm = _PDF_SECTION_HEADER_RE.match(text)
            if hm is None:
                continue
            label = _normalize_section_label(hm.group(1))
            key = (sid, label)
            if label and key not in out:
                out[key] = _LEADING_SECTION_HEADER_RE.sub("", text, count=1).strip()
    return out


def _statute_id_of_cite(cite_text: str) -> str:
    """Reduce a parenthesised statute citation ("(609/1986)") to its id ("609/1986").

    The id string is the SAME shape as :func:`_statute_id_of`'s output and the parser's
    ``target_statute_id`` (num/year), so a body keyed on a clause's governing citation and
    an op keyed on its ``target_provision_ref`` land in the same bill scope. ``str.strip``
    (no regex) keeps the semantic-plane regex census flat.
    """
    return cite_text.strip("()")


def _enacting_clause_regions(flat: str) -> list[tuple[int, int]]:
    """Absolute [head-start, terminator-end] spans of every GENUINE enacting clause.

    A genuine clause carries the amendment-verb head + statute citation "(N/YEAR)" + "§"
    before its "... seuraavasti:" terminator (the same anchors extract_enacting_clause_spans
    uses); a detailed-perustelut "... seuraavasti:" that merely discusses a provision does
    not qualify. These regions are the bills' provision LISTS ("muutetaan … 35 §, 36 §:n 3
    momentti seuraavasti:") — the "N §" refs INSIDE them must NOT be read as section-body
    headers, or the first bill's §35 body is stolen by a LATER bill's clause list.
    """
    regions: list[tuple[int, int]] = []
    for term in _TERMINATOR_RE.finditer(flat):
        w0 = max(0, term.start() - _MAX_CLAUSE_CHARS)
        window = flat[w0:term.start()]
        for h in _HE_HEAD_VERB_RE.finditer(window):
            cite = _CITE_RE.search(window, h.end(), min(len(window), h.end() + _HEAD_TO_CITE))
            if cite is not None and _PROVISION_MARK_RE.search(window, cite.end()) is not None:
                regions.append((w0 + h.start(), term.end()))
                break
    return regions


def _bill_head_scopes(flat: str) -> list[tuple[int, str]]:
    """Sorted (head-start, governing-statute-id) of every GENUINE johtolause head in ``flat``.

    A body's BILL scope is the amended act of the enacting clause that most recently PRECEDES
    it (:func:`_governing_body_statute_id`). This is derived from the HEAD positions — NOT the
    exclusion regions (:func:`_enacting_clause_regions`) — so it is robust to a region whose
    terminator-anchored span reaches back across an intervening clause: each head carries its
    OWN "(N/YEAR)" citation (the amended act the parser resolves that clause's ops against). A
    multi-verb johtolause ("kumotaan (A) 1 §, muutetaan (B) 2 § seuraavasti:") yields both heads,
    so the "greatest head-start ≤ body-pos" rule scopes the "2 §" body to B (the later amend
    head whose §-body follows), never to A. Same head+cite+"§" gate as the region recognizer.
    """
    scopes: list[tuple[int, str]] = []
    for h in _HE_HEAD_VERB_RE.finditer(flat):
        cite = _CITE_RE.search(flat, h.end(), min(len(flat), h.end() + _HEAD_TO_CITE))
        if cite is None:
            continue
        if _PROVISION_MARK_RE.search(flat, cite.end(), min(len(flat), cite.end() + _MAX_CLAUSE_CHARS)) is None:
            continue
        scopes.append((h.start(), _statute_id_of_cite(cite.group())))
    return scopes


def _governing_body_statute_id(scopes: list[tuple[int, str]], pos: int) -> str:
    """Statute id of the johtolause head that most recently precedes body offset ``pos``, else "".

    Of the genuine johtolause heads (:func:`_bill_head_scopes`) starting at/before ``pos``, the
    one with the GREATEST head-start governs — the bill whose enacting clause the body follows.
    Greatest head-start (not nearest terminator) is what makes an omnibus HE's cross-bill "N §"
    reuse resolve to the right bill, and a multi-verb johtolause's body resolve to its amend head.
    """
    best_start, best_sid = -1, ""
    for start, sid in scopes:
        if start <= pos and start > best_start:
            best_start, best_sid = start, sid
    return best_sid


def _pdf_proposed_bodies(reading_text: str, *, aggressive: bool = False) -> dict[tuple[str, str], str]:
    """Segment the bill body into (statute-id, section label) → body text, clause-region aware.

    Section-body headers are searched only OUTSIDE the enacting-clause regions
    (:func:`_enacting_clause_regions`) and after the first such clause (skipping
    detailed-perustelut prose), bounded before the rinnakkaistekstit appendix. This keeps
    a later bill's provision-list "N §" refs from being first-wins-captured as the earlier
    bill's section body.  A section body also STOPS at the next clause region (it must not
    run into the next bill's enacting clause).

    Each body is keyed by ``(governing-statute-id, section label)`` — the statute id of the
    enacting clause that most recently PRECEDES the header (its bill scope).  An omnibus HE
    routinely REUSES a section number across bills (bill A "17 §" and bill B "17 §"); a bare
    ``label`` key first-wins-collapsed the two into one body, so an op correctly matched to
    bill A's ``.../17`` was payload-compared against bill B's ``17 §`` body — a spurious
    payload_mismatch. Scoping the body to its bill keeps them distinct. First-wins within a
    single ``(statute, label)`` scope (a rinnakkaistekstit dupe of the same bill's section).
    """
    flat = _lakiehdotus_region(
        _flatten_reading_text(_strip_page_number_lines(reading_text), aggressive=aggressive),
        aggressive=aggressive,
    )
    regions = _enacting_clause_regions(flat)
    if not regions:
        # WRONG-START guard. No GENUINE enacting clause (johtolause) was found, so there is no
        # lakiehdotus provision region and thus no section body to read. The prior fallback
        # opened the scan at the first "seuraavasti:" (or, when none, at position 0) — deep in
        # the PERUSTELUT, whose detailed justifications carry their own "N §." references. That
        # is a wrong-START over-capture: the first body begins at a perustelut mention and runs
        # forward across justification prose (HE 59/1997 §1: 13.8k perustelut chars vs a 558-char
        # XML body; HE 100/2003 §3: ~1084 chars vs 71). Precision-first: emit NO bodies (the
        # payload stage then DEFERS the op, never a spurious payload_mismatch) rather than a
        # garbage perustelut body. Whenever a bill genuinely exists a region IS present (the same
        # head + "(N/YEAR)" + "§" + "seuraavasti:" signature extract_enacting_clause_spans anchors
        # on), so this drops only the no-real-clause case — it never starts a body at a LATER,
        # wrong provision (which could silently drop genuine leading body text).
        return {}
    body_start = regions[0][1]
    scopes = _bill_head_scopes(flat)

    def _in_region(pos: int) -> bool:
        return any(a <= pos < b for a, b in regions)

    def _next_region_start(pos: int) -> int:
        after = [a for a, _ in regions if a >= pos]
        return min(after) if after else len(flat)

    headers = [
        hm
        for hm in _PDF_SECTION_HEADER_RE.finditer(flat, body_start)
        if not _in_region(hm.start())
    ]
    out: dict[tuple[str, str], str] = {}
    for i, hm in enumerate(headers):
        label = _normalize_section_label(hm.group(1))
        if not label:
            continue
        sid = _governing_body_statute_id(scopes, hm.start())
        if not sid:  # precision-first: an unresolved bill scope is deferred, never guessed
            continue
        start = hm.end()
        raw_end = headers[i + 1].start() if i + 1 < len(headers) else len(flat)
        # do not spill into the next bill's enacting clause / the next section header
        end_cap = min(raw_end, _next_region_start(start))
        trailer = _PDF_BODY_TRAILER_RE.search(flat, start, end_cap)
        if trailer is not None:
            end_cap = trailer.start()
        # The commencement clause is trimmed only when it follows the substantive body's own
        # sentence-ending period (see _PDF_BODY_VOIMAANTULO_RE); a genuine voimaantulo §-body
        # (which STARTS with it, no preceding in-body period) is left whole.
        voim = _PDF_BODY_VOIMAANTULO_RE.search(flat, start, end_cap)
        if voim is not None:
            end_cap = voim.start()
        # Walk the dash-run dividers left-to-right within [start, end_cap].  ELIDE each MID-body
        # OMISSION divider (drop the run, keep the retained text on BOTH sides, keep scanning) and
        # STOP at the first END divider.  This replaces the old first-dash-wins truncation, which
        # lost a role-(a) omission body's retained tail (the dominant payload_mismatch sub-cause).
        pos = start
        segments: list[str] = []
        end = end_cap
        elided = False
        while True:
            dm = _PDF_BODY_DIVIDER_RE.search(flat, pos, end_cap)
            if dm is None:
                segments.append(flat[pos:end_cap])
                break
            segments.append(flat[pos : dm.start()])
            if _pdf_divider_is_omission(flat[dm.end() : end_cap]):
                pos = dm.end()
                elided = True
            else:  # END divider -> the section body stops here
                end = dm.start()
                break
        key = (sid, label)
        if key not in out:
            body = " ".join(segments)
            # An ELIDED capture that SPANS a page break (sentinel present from
            # _strip_page_number_lines) stitched text across page furniture whose text layer may
            # carry an unresolved hyphen seam / scattered header — a low-confidence cross-furniture
            # capture, so DEFER it (never force a payload_mismatch on a segmentation artifact).
            if elided and _PDF_PAGE_BREAK_SENTINEL in body:
                continue
            body = _WS_RUN_RE.sub(" ", body.replace(_PDF_PAGE_BREAK_SENTINEL, " ")).strip()
            # Next-section TITLE over-capture: a section otsikko sits in reading order just BEFORE
            # the following "N §" number (see _looks_like_section_title), so a body that ran all
            # the way up to that next header (no furniture / voimaantulo / END divider fired first,
            # i.e. end is still the next-header boundary raw_end) swallowed the next section's
            # title heading. Trim it off the body's tail — the last sentence-ending period splits
            # the body's own final sentence from the trailing title, so keep everything through
            # that period and drop only a short Capitalized title after it. Precision-first: a
            # genuine trailing SENTENCE ends in a period, so nothing follows the last period to
            # trim — this never truncates real body prose; ``p > 0`` keeps the body non-empty.
            if i + 1 < len(headers) and end == raw_end:
                p = body.rfind(".")
                if p > 0:
                    tail = body[p + 1 :].strip().strip('"“”')
                    if _looks_like_section_title(tail):
                        body = body[: p + 1]
            body = _PDF_BODY_TRAILING_PAGENUM_RE.sub("", body)
            out[key] = body
    return out


#: Minimum fraction of the XML proposed body's words that must be present in the PDF
#: segment for the two to be the SAME provision body and thus payload-comparable.  Below
#: this the PDF segment did not capture this provision (geom scrambles the HE bill body /
#: two-column rinnakkaistekstit), so the comparison is TYPE-DEFERRED — the op-structure
#: stage already proved the provision target matches, so a near-zero-overlap body is a
#: segmentation miss, NOT a genuine "the HE proposes different text" mismatch.  Deferring
#: it avoids a spurious payload_mismatch (AGENTS.md: never force a diff).
_PAYLOAD_OVERLAP_MIN = 0.5

_WORD_RE = re.compile(r"\w+", re.UNICODE)


def _word_overlap(xml_text: str, pdf_text: str) -> float:
    """Fraction of the XML body's word multiset covered by the PDF body's word set."""
    xw = _WORD_RE.findall((xml_text or "").lower())
    if not xw:
        return 0.0
    pw = set(_WORD_RE.findall((pdf_text or "").lower()))
    return sum(1 for w in xw if w in pw) / len(xw)


@dataclass(frozen=True, slots=True)
class PayloadDiffResult:
    """Outcome of the payload stage over one HE's matched proposed ops."""

    divergences: tuple[OpDivergence, ...]
    compared: int
    deferred: int
    skipped: int


#: DIAGNOSTIC-ONLY display toggle (does NOT touch the equality decision, the fold set,
#: or any counting). When set, a ``payload_mismatch`` divergence's ``detail`` carries the
#: FULL canonical XML/PDF payloads instead of the 80-char truncated preview, so the residual
#: JSONL can be adjudicated into sub-causes off-line. Off by default → text/JSON unchanged.
_EMIT_FULL_PAYLOADS = False


def set_emit_full_payloads(flag: bool) -> None:
    """Diagnostic switch: emit untrimmed payloads in ``payload_mismatch`` details."""
    global _EMIT_FULL_PAYLOADS
    _EMIT_FULL_PAYLOADS = bool(flag)


def _payload_preview(text: str) -> str:
    """Whitespace-flatten a canonical payload for the detail string.

    Truncates to :data:`_PAYLOAD_CANON_TRIM` unless the diagnostic
    :data:`_EMIT_FULL_PAYLOADS` toggle is set (then the full body is emitted).
    """
    flat = " ".join(text.split())
    if _EMIT_FULL_PAYLOADS:
        return flat
    return flat if len(flat) <= _PAYLOAD_CANON_TRIM else flat[:_PAYLOAD_CANON_TRIM] + "…"


def diff_proposed_payloads(
    xml_bodies: dict[tuple[str, str], str],
    pdf_bodies: dict[tuple[str, str], str],
    matched_ops: tuple[HEFlatOp, ...],
) -> PayloadDiffResult:
    """Compare the proposed BODY TEXT of each matched op across witnesses.

    REPEAL / commencement / expiry ops are skipped (no proposed body); a target whose
    body is absent on either witness — or whose PDF segment shares too few words with the
    XML body to be the same provision (a geom segmentation miss) — is TYPE-DEFERRED;
    otherwise the two bodies are compared with :func:`text_equivalence` and a surviving
    residual becomes a ``payload_mismatch``.

    Both body maps are keyed by ``(statute-id, section label)`` (the op's bill scope), so
    an omnibus HE's cross-bill "N §" reuse never pairs bill A's "17 §" body against bill B's
    "17 §" op. Each ``(statute, label)`` scope is compared once (first op in it).
    """
    out: list[OpDivergence] = []
    compared = deferred = skipped = 0
    seen_labels: set[tuple[str, str]] = set()
    for op in matched_ops:
        if op.action in ("repeal", "commencement", "expiry"):
            skipped += 1
            continue
        label = _section_label_of(op.target_ref)
        key = (_statute_id_of(op.target_ref), label)
        if not label or key in seen_labels:
            deferred += 1
            continue
        seen_labels.add(key)
        xml_text = xml_bodies.get(key)
        pdf_text = pdf_bodies.get(key)
        if xml_text is None or pdf_text is None:
            deferred += 1
            continue
        if _word_overlap(xml_text, pdf_text) < _PAYLOAD_OVERLAP_MIN:
            # PDF segment did not capture this provision's body (geom scramble) — defer.
            deferred += 1
            continue
        compared += 1
        eq = text_equivalence(xml_text, pdf_text)
        if eq.residual:
            folds = ",".join(f.value for f in eq.folds) or "none"
            out.append(
                OpDivergence(
                    kind="payload_mismatch",
                    target_ref=op.target_ref,
                    xml_op=op.render,
                    pdf_op=op.render,
                    detail=(
                        f"proposed body differs beyond inert encoding (folds fired: {folds}); "
                        f"xml={_payload_preview(eq.left_canon)!r} pdf={_payload_preview(eq.right_canon)!r}"
                    ),
                )
            )
    return PayloadDiffResult(tuple(out), compared, deferred, skipped)


# --------------------------------------------------------------------------- #
# XML-empty classification (typed benign / deferred strata).                   #
# --------------------------------------------------------------------------- #


def _xml_body_len(xml_bytes: bytes) -> int:
    from lxml import etree

    try:
        root = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError:
        return 0
    mb = root.find(f".//{{{_AKN_NS}}}mainBody")
    return len(_element_text(mb)) if mb is not None else 0


def _has_pdf_component_ref(xml_bytes: bytes) -> bool:
    return bool(re.search(rb"\.pdf|/media/", xml_bytes))


def _classify_xml_empty(xml_bytes: bytes, branch: HEParsedBranch) -> tuple[str, str]:
    """Classify an HE whose trusted XML lowered to zero comparable amendment ops.

    Returns ``(compare_status, detail)`` — one of ``xml_wrapper_only`` (thin body + a
    ``.pdf``/``media`` reference and no inline enacting clause), ``not_applicable``
    (no enactment clauses at all), ``new_statute_only`` (only new-law "säädetään"
    enactments), or ``xml_parse_incomplete`` (an amendment-verb clause the XML parser
    could not lower — an XML-side gap, deferred).
    """
    if (
        branch.enactment_sections_found == 0
        and _has_pdf_component_ref(xml_bytes)
        and _xml_body_len(xml_bytes) < _XML_WRAPPER_BODY_MAX_CHARS
    ):
        return (
            "xml_wrapper_only",
            "HE main.xml is a thin PDF-wrapper (no inline enacting clause, .pdf/media "
            "component ref) — content is PDF-only, comparison deferred to the PDF-as-source path",
        )
    if branch.parse_status == HEParseStatus.NOT_APPLICABLE or branch.enactment_sections_found == 0:
        return (
            "not_applicable",
            "no enactment clauses (treaty ratification / budget / purely-rationale HE) — "
            "no proposed law effects to compare",
        )
    # Enactment clauses present but zero amendment ops. Distinguish a pure new-statute
    # enactment ("säädetään:", no amendment verb) from a genuine XML parse gap.
    any_amend = any(
        _AMEND_VERB_RE.search(op.source_span_text) for op in branch.proposed_ops
    )
    findings_text = " ".join(
        getattr(f, "clause_text", "") for f in branch.parse_findings
    )
    if not any_amend and not _AMEND_VERB_RE.search(findings_text):
        return (
            "new_statute_only",
            "every enacting clause enacts a NEW law (säädetään) — no amendment operations "
            "against an existing statute to compare",
        )
    return (
        "xml_parse_incomplete",
        "amendment-verb enacting clause present but the trusted XML parser lowered it to "
        "zero ops (XML-side parse gap, not a PDF defect) — deferred",
    )


# --------------------------------------------------------------------------- #
# Top-level comparison + report.                                              #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True, slots=True)
class HECompareResult:
    """The full outcome of one HE's XML↔PDF proposed-effect IR comparison."""

    he_id: str
    branch_id: str
    compare_status: str
    divergences: tuple[OpDivergence, ...]
    xml_op_count: int
    pdf_op_count: int
    detail: str = ""
    payload_compared: int = 0
    payload_deferred: int = 0
    payload_skipped: int = 0

    @property
    def counts(self) -> dict[str, int]:
        c = {k: 0 for k in DIVERGENCE_KINDS}
        for d in self.divergences:
            c[d.kind] = c.get(d.kind, 0) + 1
        return c

    @property
    def typed_divergence_count(self) -> int:
        return sum(1 for d in self.divergences if d.kind != _BENIGN_MATCH)

    @property
    def exact_equivalent(self) -> bool:
        """True iff the PDF proposed-op IR is EXACTLY the XML IR (zero typed divergences).

        Only meaningful when ``compare_status == "compared"``.
        """
        return self.compare_status == "compared" and self.typed_divergence_count == 0


def compare_he(
    xml_bytes: bytes,
    reading_text: str,
    *,
    he_year: int,
    he_number: int,
    he_id: Optional[str] = None,
    classify_fn: "Optional[Callable[[str], object]]" = None,
) -> HECompareResult:
    """Diff an HE's proposed-op IR from its two witnesses (XML bytes + PDF reading text).

    Pure over its two inputs (no farchive / geom) so CI exercises it hermetically.  The
    XML side is the trusted :func:`parse_he_branch`; the PDF side segments enacting-clause
    spans out of ``reading_text`` and lowers them through the IDENTICAL
    ``_parse_one_clause``.  Typed benign/deferred strata are returned as a status, never
    raised.

    ``classify_fn`` is OPTIONAL and defaults to the mechanical, char-bounded
    :func:`extract_enacting_clause_spans` (no behaviour change).  When supplied it selects
    the LLM-gated :func:`extract_enacting_clause_spans_llm` instead — the injected classifier
    (real use: cache-through ``classify_candidate_cached``) removes the char bound so a
    whole mega-amendment bill's johtolause is recovered rather than dropped.  The LLM only
    SEGMENTS; the spans still flow through the SAME ``_parse_one_clause`` and are EXACT-diffed
    against the trusted XML, so the exactness invariant is untouched.
    """
    hid = he_id or f"HE {he_number}/{he_year} vp"
    branch = parse_he_branch(xml_bytes, he_year=he_year, he_number=he_number, he_id=hid)
    branch_id = branch.branch_id
    xml_flat = flatten_branch_ops(branch.proposed_ops)

    if not xml_flat:
        status, detail = _classify_xml_empty(xml_bytes, branch)
        return HECompareResult(hid, branch_id, status, (), 0, 0, detail)

    # PDF witness. Try the NORMAL path first; only when it finds NO enacting clause at all
    # (would-be ``pdf_no_clause``) do we RETRY with the reading-fidelity recoveries turned on
    # (``aggressive``: slash-as-"1" cite repair, preceding-TOC-leader appendix uncut, annex/luku
    # target). Gating the recoveries to this fallback keeps every already-detected HE's result
    # byte-identical to the normal path (0 collateral), while rescuing an HE whose johtolause was
    # invisible only because of a text-layer defect. Both passes lower the SAME spans through the
    # SAME ``_parse_one_clause`` and EXACT-diff them against the trusted XML.
    result = _compare_pdf_witness(
        xml_bytes, reading_text, xml_flat, hid, branch_id, classify_fn, aggressive=False
    )
    if result.compare_status == "pdf_no_clause":
        recovered = _compare_pdf_witness(
            xml_bytes, reading_text, xml_flat, hid, branch_id, classify_fn, aggressive=True
        )
        if recovered.compare_status == "compared":
            return recovered
    return result


def _compare_pdf_witness(
    xml_bytes: bytes,
    reading_text: str,
    xml_flat: tuple[HEFlatOp, ...],
    hid: str,
    branch_id: str,
    classify_fn: "Optional[Callable[[str], object]]",
    *,
    aggressive: bool,
) -> HECompareResult:
    """Segment the PDF witness, diff against ``xml_flat``, and run the payload stage.

    ``aggressive`` is threaded through the extraction / body-segmentation so the fallback pass
    (see :func:`compare_he`) reads with the reading-fidelity recoveries enabled; the normal pass
    (``aggressive=False``) is byte-identical to the pre-existing behaviour.
    """
    if classify_fn is None:
        spans = extract_enacting_clause_spans(reading_text, aggressive=aggressive)
    else:
        spans = extract_enacting_clause_spans_llm(
            reading_text, classify_fn=classify_fn, aggressive=aggressive
        )
    pdf_ops: list = []
    for span in spans:
        new_ops, _findings = _parse_one_clause(span, len(pdf_ops), hid, branch_id)
        pdf_ops.extend(new_ops)
    pdf_flat = flatten_branch_ops(tuple(pdf_ops))

    if not pdf_flat:
        return HECompareResult(
            hid,
            branch_id,
            "pdf_no_clause",
            (),
            len(xml_flat),
            0,
            "PDF reading text yielded no extractable enacting clause (lakiehdotus beyond "
            "the page window or a scanned HE) — deferred, not forced into an all-missing diff",
        )

    divergences = diff_proposed_ops(xml_flat, pdf_flat)
    # Retype XML-absent op_extra blocks by their GOVERNING HEAD in the PDF reading text — a
    # real bill title (genuine second bill) or a consequential-repeal formula (a real effect
    # the XML omits) → first-class witness disagreement; neither → phantom DEFECT stays
    # op_extra_in_pdf. Label-independent (never reads the XML op-set to decide a type).
    reclass_flat = _lakiehdotus_region(
        _flatten_reading_text(reading_text, aggressive=aggressive), aggressive=aggressive
    )
    divergences = _reclassify_out_of_scope_second_bills(divergences, xml_flat, reclass_flat)

    matched_refs = {d.target_ref for d in divergences if d.kind == _BENIGN_MATCH}
    matched_ops = tuple(op for op in xml_flat if op.target_ref in matched_refs)
    xml_bodies = _xml_proposed_bodies(xml_bytes)
    pdf_bodies = _pdf_proposed_bodies(reading_text, aggressive=aggressive)
    payload = diff_proposed_payloads(xml_bodies, pdf_bodies, matched_ops)

    return HECompareResult(
        hid,
        branch_id,
        "compared",
        divergences + payload.divergences,
        len(xml_flat),
        len(pdf_flat),
        payload_compared=payload.compared,
        payload_deferred=payload.deferred,
        payload_skipped=payload.skipped,
    )


# --------------------------------------------------------------------------- #
# Farchive-backed witness reads (geom lane, free for born-digital HEs).        #
# --------------------------------------------------------------------------- #


#: Below this many non-space chars the pdfium text layer is absent/sparse (a scanned HE
#: or an image-only PDF) → fall back to the geom bbox reconstruction.
_TEXT_LAYER_MIN_CHARS = 400


def _pdfium_text_layer(data: bytes, max_pages: int) -> str:
    """Native pdfium text-layer extraction (the PDF's own text order), newline-joined.

    For a BORN-DIGITAL HE the embedded text layer already reads in the correct order.
    The geom bbox RECONSTRUCTION, by contrast, re-derives reading order from glyph
    geometry and SCATTERS two-column bill layouts — it interleaves bill-body prose and
    page furniture INTO the enacting clause's provision list, splitting "uusi 11 kohta"
    and dropping the trailing provisions (op_missing) or inventing cross-statute ones
    (op_extra). Using the native text order recovers those (measured: op_missing −71%,
    op_extra −63% on the diagnosed HEs). pdfium is not thread-safe → hold the systemic lock.
    """
    # A missing backend must NOT be swallowed as an empty/clean read: catch the specific
    # import failure (ModuleNotFoundError, and the None-in-sys.modules ImportError) and
    # re-raise it as the distinct HEReaderUnavailableError so it propagates past the per-HE
    # ``error`` typing rather than masquerading as "0 residual". Narrow catch, never bare.
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise HEReaderUnavailableError(
            "fi-he-ir-compare: PDF text backend pypdfium2 is unavailable — the PDF witness "
            "cannot be read; a missing backend must never be reported as a clean empty "
            "result (install with `uv sync --extra pdf`)"
        ) from exc

    from lawvm.ingest.visual import PDFIUM_LOCK

    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(data)
        try:
            n = min(len(pdf), max_pages)
            return "\n".join(pdf[i].get_textpage().get_text_range() for i in range(n))
        finally:
            pdf.close()


def he_pdf_reading_text(
    farchive: str, pdf_locator: str, *, max_pages: int = 5000
) -> str:
    """Reconstruct an HE main.pdf's reading text (born-digital, zero image tokens).

    Reads ALL pages up to ``max_pages`` because the lakiehdotus (bill text) sits near the
    END of the HE, after the perustelut — and in a GIANT multi-bill omnibus HE the later
    bills sit hundreds of pages in (HE 61/2018 is 1760 pages, its bills printed
    alphabetically).  The cap is deliberately high (5000, guarded by ``_MAX_HE_PDF_BYTES``
    for pathological outliers): a low page cap SILENTLY drops whole late bills from the
    op-set (the dominant op_missing cause on mega-omnibus HEs), and the native text-layer
    read is zero-token so there is no reason to truncate a born-digital document early.
    PRIMARY lane is the native pdfium TEXT LAYER (correct reading order for born-digital
    HEs); it falls back to the geom bbox reconstruction only when the text layer is
    absent/sparse (a scanned HE).
    """
    import hashlib
    from datetime import datetime, timezone

    from farchive import Farchive
    from lawvm.core.source_document.extraction import SourceManifestation
    from lawvm.tools.fi_producer_compare import GeomProducer, _load_pages

    fa = Farchive(farchive)
    try:
        data = fa.get(pdf_locator)
    finally:
        fa.close()
    if not data:
        raise HEIrCompareError(f"fi-he-ir-compare: HE main.pdf not found: {pdf_locator}")
    text_layer = _pdfium_text_layer(data, max_pages)
    if len(re.sub(r"\s+", "", text_layer)) >= _TEXT_LAYER_MIN_CHARS:
        return text_layer
    # No usable text layer (scanned / image-only) → geom bbox reconstruction.
    man = SourceManifestation(
        artifact_digest=hashlib.sha256(data).hexdigest(),
        source_bytes=data,
        locator=pdf_locator,
        source_role="gazette",
        fetched_at=datetime.now(tz=timezone.utc),
        media_type="application/pdf",
    )
    pages = _load_pages(man, max_pages)
    return "\n".join(GeomProducer().reconstruct_pages(man, pages))


def compare_he_from_farchive(
    farchive: str,
    he_year: int,
    he_number: int,
    *,
    he_id: Optional[str] = None,
    lang: str = "fin",
    max_pages: int = 5000,
    classify_fn: "Optional[Callable[[str], object]]" = None,
) -> HECompareResult:
    """Read both HE witnesses from the farchive and run :func:`compare_he`.

    XML from ``.../fin@/main.xml``; PDF reading text from ``.../fin@/main.pdf`` via the
    free geom lane.  A missing/unreadable witness surfaces as an ``error`` status.

    ``classify_fn`` is passed straight through to :func:`compare_he` — ``None`` (default)
    keeps the mechanical enacting-clause segmentation; an injected classifier switches the
    PDF side onto the LLM-gated span extractor.
    """
    from farchive import Farchive

    base = f"{_AKN_PATH_PREFIX}{he_year}/{he_number}/{lang}@/"
    hid = he_id or f"HE {he_number}/{he_year} vp"
    branch_id = f"fi/he/{he_year}/{he_number}"
    fa = Farchive(farchive)
    try:
        xml_bytes = fa.get(base + "main.xml")
        pdf_bytes = fa.get(base + "main.pdf")
    finally:
        fa.close()
    if not xml_bytes:
        return HECompareResult(
            hid, branch_id, "error", (), 0, 0, f"HE main.xml not found: {base}main.xml"
        )
    # Guard the rare pathological giant PDF: skip the read (which would hang the whole
    # sweep) and TYPE it, so the corpus completes and the skip is visible, not silent.
    if pdf_bytes and len(pdf_bytes) > _MAX_HE_PDF_BYTES:
        return HECompareResult(
            hid,
            branch_id,
            "pdf_oversize",
            (),
            0,
            0,
            f"HE main.pdf is {len(pdf_bytes) // 1_000_000} MB (> "
            f"{_MAX_HE_PDF_BYTES // 1_000_000} MB) — geom read skipped to keep the sweep "
            "live; type-deferred, not diffed",
        )
    try:
        reading_text = he_pdf_reading_text(farchive, base + "main.pdf", max_pages=max_pages)
    except HEReaderUnavailableError:
        # A missing PDF backend is an environment failure, never a benign per-HE skip: let
        # it propagate so a backend-less sweep fails LOUDLY, instead of typing every HE
        # ``error`` (a non-compared status an aggregate would read as clean). Absence of a
        # witness must never read as absence of divergence.
        raise
    except Exception as exc:  # a bad/unreadable PDF is a typed status, never a crash
        return HECompareResult(
            hid,
            branch_id,
            "error",
            (),
            0,
            0,
            f"HE main.pdf read failed: {type(exc).__name__}: {exc}",
        )
    return compare_he(
        xml_bytes,
        reading_text,
        he_year=he_year,
        he_number=he_number,
        he_id=he_id,
        classify_fn=classify_fn,
    )


def result_to_json(result: HECompareResult) -> dict:
    return {
        "he_id": result.he_id,
        "branch_id": result.branch_id,
        "compare_status": result.compare_status,
        "detail": result.detail,
        "xml_op_count": result.xml_op_count,
        "pdf_op_count": result.pdf_op_count,
        "counts": result.counts,
        "typed_divergence_count": result.typed_divergence_count,
        "exact_equivalent": result.exact_equivalent,
        "payload_compared": result.payload_compared,
        "payload_deferred": result.payload_deferred,
        "payload_skipped": result.payload_skipped,
        "divergences": [
            {
                "kind": d.kind,
                "target_ref": d.target_ref,
                "xml_op": d.xml_op,
                "pdf_op": d.pdf_op,
                "detail": d.detail,
            }
            for d in result.divergences
        ],
    }


_KIND_GLYPH = {
    "matched": "=",
    "op_missing_in_pdf": "-",
    "op_extra_in_pdf": "+",
    "kind_mismatch": "~",
    "payload_mismatch": "≠",
    _PDF_OUT_OF_SCOPE_STATUTE: "≈",
    _PDF_CONSEQUENTIAL_REPEAL: "≈",
}


def _print_result(result: HECompareResult) -> None:
    print(f"fi-he-ir-compare  {result.he_id}  ({result.branch_id})")
    print("=" * 78)
    if result.compare_status != "compared":
        benign = result.compare_status != "error"
        print(f"STATUS: {result.compare_status}  ({'benign/deferred' if benign else 'error'})")
        print(f"  {result.detail}")
        if result.xml_op_count:
            print(f"  (XML→ops produced {result.xml_op_count} proposed ops)")
        return
    c = result.counts
    print(
        f"XML ops={result.xml_op_count}  PDF ops={result.pdf_op_count}   "
        f"matched={c['matched']}  op_missing_in_pdf={c['op_missing_in_pdf']}  "
        f"op_extra_in_pdf={c['op_extra_in_pdf']}  kind_mismatch={c['kind_mismatch']}  "
        f"payload_mismatch={c['payload_mismatch']}  "
        f"pdf_out_of_scope_statute={c.get(_PDF_OUT_OF_SCOPE_STATUTE, 0)}  "
        f"pdf_consequential_repeal={c.get(_PDF_CONSEQUENTIAL_REPEAL, 0)}"
    )
    print(
        f"  payload stage: compared={result.payload_compared}  "
        f"deferred={result.payload_deferred}  no_body_skipped={result.payload_skipped}"
    )
    print("-" * 78)
    for d in result.divergences:
        g = _KIND_GLYPH.get(d.kind, "?")
        if d.kind == "matched":
            print(f"  {g} {d.target_ref:<28} {d.xml_op}")
        elif d.kind == "op_missing_in_pdf":
            print(f"  {g} {d.target_ref:<28} XML:{d.xml_op}  (dropped by PDF)")
        elif d.kind == "op_extra_in_pdf":
            print(f"  {g} {d.target_ref:<28} PDF:{d.pdf_op}  (not in XML)")
        elif d.kind == _PDF_OUT_OF_SCOPE_STATUTE:
            print(f"  {g} {d.target_ref:<28} PDF:{d.pdf_op}  (out-of-scope 2nd bill; witness disagreement)")
        elif d.kind == _PDF_CONSEQUENTIAL_REPEAL:
            print(f"  {g} {d.target_ref:<28} PDF:{d.pdf_op}  (consequential repeal in commencement clause; witness disagreement)")
        elif d.kind == "payload_mismatch":
            print(f"  {g} {d.target_ref:<28} {d.detail}")
        else:
            print(f"  {g} {d.target_ref:<28} xml={d.xml_op}  pdf={d.pdf_op}")
    print("-" * 78)
    verdict = (
        "PASS (exact proposed-op equivalence)"
        if result.exact_equivalent
        else f"FAIL ({result.typed_divergence_count} typed divergence(s) to escalate)"
    )
    print(
        f"EXACT proposed-op equivalence: {verdict}   "
        f"[{c['matched']}/{len(result.divergences)} ops matched]"
    )


def main(args: argparse.Namespace) -> None:
    """CLI handler for ``lawvm fi-he-ir-compare``."""
    if getattr(args, "full_payloads", False):
        set_emit_full_payloads(True)
    farchive = args.farchive or _DEFAULT_FARCHIVE
    m = re.match(r"^(\d{4})/(\d{1,5})$", str(args.he or ""))
    if not m:
        raise SystemExit("fi-he-ir-compare: pass an HE as YEAR/NUM (e.g. 2024/1)")
    he_year, he_number = int(m.group(1)), int(m.group(2))
    result = compare_he_from_farchive(
        farchive, he_year, he_number, lang=args.lang, max_pages=args.max_pages
    )
    if args.json:
        print(json.dumps(result_to_json(result), ensure_ascii=False, indent=2))
    else:
        _print_result(result)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(result_to_json(result), fh, ensure_ascii=False, indent=2)
