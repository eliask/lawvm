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
    (born-digital lakiehdotus beyond the page window, or a scanned HE the geom lane
    returns nothing for); deferred rather than forced into an all-ops-missing diff.

The extraction here (enacting-clause span segmentation + per-section body payloads) is
deliberately reusable toward the FULL-HE-structure goal (extracting every section — the
general + detailed perustelut AND the lakiehdotus bill texts — as verified IR); this
module's deliverable is the narrower proposed-EFFECT op equivalence.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from typing import Optional

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
_CITE_RE = re.compile(r"\(\d{1,5}/\d{4}\)")

#: Max distance from an amendment-verb head to the statute citation it governs. Must
#: span a full Finnish law TITLE, which for EU-implementation acts is long ("lisätään
#: hallinnollisesta yhteistyöstä verotuksen alalla … annettuun lakiin (185/2013)" ≈ 238
#: chars) — at 160 the second bill of a multi-bill HE was dropped (op_missing). The head
#: verb matches only enacting-PRESENT forms (not the conditional perustelut discuss in),
#: and a "§" + "seuraavasti" are still required in-window, so a wide title budget does not
#: admit perustelut prose.
_HEAD_TO_CITE = 400

#: A provision marker ("§") — a genuine amendment directive lists the provisions it
#: touches (7 §, 9 §:n 2 momentti, ...) between its statute citation and "seuraavasti:".
#: Requiring one INSIDE the candidate span is the structural discriminator that separates
#: an enacting clause from a stray perustelut sentence ("muutetaan lakia X merkittävästi
#: ... seuraavasti:") — and, unlike the enactment formula, it is ALWAYS co-located with
#: the clause, so geom scattering the centered "... päätöksen mukaisesti" formula far from
#: its clause does not cause the (genuine) clause to be dropped.
_PROVISION_MARK_RE = re.compile(r"§")

#: Any amendment verb (vs the new-law-only "säädetään"): tells a proposed-AMENDMENT HE
#: apart from a pure new-statute enactment when the XML lowers to zero ops.
_AMEND_VERB_RE = re.compile(
    r"\b(muut(?:etaan|tanut|ettu)|lis[äa]t|kumo|korv|poist|siirr)", re.IGNORECASE
)

#: Bounded window back from a "... seuraavasti:" terminator (AGENTS.md §1.11 bound).
_MAX_CLAUSE_CHARS = 2400

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

#: The per-page running header "HE <n>/<year> vp" (with its adjacent page number) that the
#: text layer emits at every page top/bottom. It lands MID-body when a provision spans a
#: page break ("…joka 4 HE 84/1998 vp omistajan…"), breaking payload equality. It is never
#: part of a proposed body, so deleting it (and the immediately-adjacent page digits) is
#: safe. Bounded/flat quantifiers (FW-07). "HE n/year vp" ≠ a "(n/year)" statute citation,
#: so this does not touch the enacting-clause citation anchor.
_PAGE_FURNITURE_RE = re.compile(
    r"\d{0,4}\s{0,3}HE\s{1,3}\d{1,4}/\d{4}\s{1,3}vp\s{0,3}\d{0,4}", re.IGNORECASE
)


def _flatten_reading_text(reading_text: str) -> str:
    """De-hyphenate, strip per-page running headers, and whitespace-flatten reading text."""
    text = dehyphenate(reading_text or "")
    text = _PAGE_FURNITURE_RE.sub(" ", text)
    return re.sub(r"[ \t\r\n­]+", " ", text).strip()


def _lakiehdotus_region(flat: str) -> str:
    """Truncate flattened reading text at the first Rinnakkaistekstit/Liitteet heading.

    The bill directives (lakiehdotus) always precede the parallel-texts appendix, so
    cutting at the first appendix heading drops the spurious enacting-clause spans the
    two-column reprint would otherwise yield — without touching a genuine directive.
    No heading present (the common case: HEs with no rinnakkaistekstit) → unchanged.
    """
    m = _LAKIEHDOTUS_END_RE.search(flat)
    return flat[: m.start()] if m else flat


def extract_enacting_clause_spans(
    reading_text: str, *, max_clause_chars: int = _MAX_CLAUSE_CHARS
) -> list[str]:
    """Segment PDF reading text into enacting-clause spans (named recognizer).

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
    """
    flat = _lakiehdotus_region(_flatten_reading_text(reading_text))
    spans: list[str] = []
    seen: set[tuple[int, int]] = set()
    for head in _HE_HEAD_VERB_RE.finditer(flat):
        hstart = head.start()
        # The head must govern a statute citation just after it.
        cite = _CITE_RE.search(flat, head.end(), min(len(flat), head.end() + _HEAD_TO_CITE))
        if cite is None:
            continue
        term = _TERMINATOR_RE.search(flat, hstart, hstart + max_clause_chars)
        if term is None:
            continue
        end = term.end()
        # A genuine amendment directive lists provisions ("§") it touches; a stray
        # perustelut sentence with an amendment verb + citation does not.
        if _PROVISION_MARK_RE.search(flat, cite.end(), end) is None:
            continue
        key = (hstart, end)
        if key in seen:
            continue
        seen.add(key)
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
# Payload stage — proposed body-text equivalence for MATCHED ops.              #
# --------------------------------------------------------------------------- #
#
# The op-structure diff proves both witnesses name the SAME proposed provision + verb.
# The payload stage proves the PROPOSED BODY TEXT they carry for that provision is the
# same too, modulo ``op_equivalence.text_equivalence``'s inert quotient.  The proposed
# text lives in the bill body (XML: the statuteProvisionsWrapper <section> bodies; PDF:
# the bill text after "... seuraavasti:").  Both are keyed by section label; a label
# absent (or ambiguous across bills) on either witness is TYPE-DEFERRED — counted, never
# forced into a spurious payload_mismatch.  REPEAL / commencement ops carry no body.

#: Section-body header inside a bill's PDF reading text ("7 §", "2 a §"); the
#: ``(?!\s{0,2}:)`` guard rejects a case-inflected cross-reference ("4 §:n 1 kohta").
_PDF_SECTION_HEADER_RE = re.compile(r"(\d{1,4}\s{0,3}[a-zä]?)\s{0,3}§(?!\s{0,2}:)", re.IGNORECASE)

#: Leading "N §" address header stripped from a payload so the comparison is over prose.
_LEADING_SECTION_HEADER_RE = re.compile(r"^\s{0,4}\d{1,4}\s{0,3}[a-zä]?\s{0,3}§\s*", re.IGNORECASE)

#: A section body's trailer boundary: the entry-into-force divider ("———"/"—————", a run
#: of 3+ dash glyphs) or a bill/appendix/rinnakkaistekstit heading.  The last section of
#: a bill otherwise runs to the next "N §" (or EOF) and swallows the voimaantulo clause /
#: parallel-texts / appendix that the trusted XML section body never carries — a spurious
#: payload_mismatch.  We bound each PDF section body at the FIRST such marker.
_PDF_BODY_TRAILER_RE = re.compile(
    r"(?:[—–\-—–]{3,}|\bRinnakkaistekstit\b|\bLiitteet?\b|\bVoimassa\s+oleva\s+laki\b|\bEhdotus\b)",
    re.IGNORECASE,
)

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


def _xml_proposed_bodies(xml_bytes: bytes) -> dict[str, str]:
    """Map section label → proposed body text from the HE bill statuteProvisionsWrapper.

    First-wins on a duplicate label (a label recurring across bills is ambiguous and the
    payload stage will simply have one entry; a genuinely colliding second body is left
    for the deferral path).  Legacy enactment <section> children are also indexed.
    """
    from lxml import etree

    try:
        root = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError:
        return {}
    out: dict[str, str] = {}
    for el in root.iter():
        if el.attrib.get("name") != _PAYLOAD_WRAPPER_NAME:
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
            if label and label not in out:
                out[label] = _LEADING_SECTION_HEADER_RE.sub("", text, count=1).strip()
    return out


def _lakiehdotus_body_start(flat: str) -> int:
    """Index after the FIRST GENUINE enacting-clause terminator (skips perustelut prose).

    A detailed-perustelut sentence may contain "... seuraavasti:" AND discuss provisions
    ("Voimassa oleva 13 § korvattaisiin uudella 13 §:llä"), but only a real enacting clause
    carries the amendment-verb head + statute citation "(N/YEAR)" + "§" before its
    terminator. Bill-body extraction must start after the first such GENUINE terminator, so
    the perustelut's "N §" discussions are not first-wins-captured as bill sections (which
    truncated e.g. HE 2020/210 §13 to a 58-char perustelut fragment). Falls back to the
    first terminator when no genuine clause is found (single born-digital bill).
    """
    for term in _TERMINATOR_RE.finditer(flat):
        w0 = max(0, term.start() - _MAX_CLAUSE_CHARS)
        window = flat[w0:term.start()]
        for h in _HE_HEAD_VERB_RE.finditer(window):
            cite = _CITE_RE.search(window, h.end(), min(len(window), h.end() + _HEAD_TO_CITE))
            if cite is not None and _PROVISION_MARK_RE.search(window, cite.end()) is not None:
                return term.end()
    first = _TERMINATOR_RE.search(flat)
    return first.end() if first is not None else 0


def _pdf_proposed_bodies(reading_text: str) -> dict[str, str]:
    """Segment the bill body after "... seuraavasti:" into section label → body text.

    Starts after the first GENUINE enacting-clause terminator (:func:`_lakiehdotus_body_start`
    — skips detailed-perustelut prose) and ends before the rinnakkaistekstit appendix, so
    only true bill sections are indexed.  First-wins on a duplicate label; a label seen twice
    (rinnakkaistekstit repeats the bill) keeps the first and drops later colliding bodies to
    the deferral path.
    """
    flat = _lakiehdotus_region(_flatten_reading_text(reading_text))
    body = flat[_lakiehdotus_body_start(flat):]
    headers = list(_PDF_SECTION_HEADER_RE.finditer(body))
    out: dict[str, str] = {}
    for i, hm in enumerate(headers):
        label = _normalize_section_label(hm.group(1))
        if not label:
            continue
        start = hm.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(body)
        trailer = _PDF_BODY_TRAILER_RE.search(body, start, end)
        if trailer is not None:
            end = trailer.start()
        if label not in out:
            out[label] = body[start:end].strip()
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


def _trim(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= _PAYLOAD_CANON_TRIM else flat[:_PAYLOAD_CANON_TRIM] + "…"


def diff_proposed_payloads(
    xml_bodies: dict[str, str],
    pdf_bodies: dict[str, str],
    matched_ops: tuple[HEFlatOp, ...],
) -> PayloadDiffResult:
    """Compare the proposed BODY TEXT of each matched op across witnesses.

    REPEAL / commencement / expiry ops are skipped (no proposed body); a target whose
    body is absent on either witness — or whose PDF segment shares too few words with the
    XML body to be the same provision (a geom segmentation miss) — is TYPE-DEFERRED;
    otherwise the two bodies are compared with :func:`text_equivalence` and a surviving
    residual becomes a ``payload_mismatch``.  Section labels are matched once (first op
    per label).
    """
    out: list[OpDivergence] = []
    compared = deferred = skipped = 0
    seen_labels: set[str] = set()
    for op in matched_ops:
        if op.action in ("repeal", "commencement", "expiry"):
            skipped += 1
            continue
        label = _section_label_of(op.target_ref)
        if not label or label in seen_labels:
            deferred += 1
            continue
        seen_labels.add(label)
        xml_text = xml_bodies.get(label)
        pdf_text = pdf_bodies.get(label)
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
                        f"xml={_trim(eq.left_canon)!r} pdf={_trim(eq.right_canon)!r}"
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
) -> HECompareResult:
    """Diff an HE's proposed-op IR from its two witnesses (XML bytes + PDF reading text).

    Pure over its two inputs (no farchive / geom) so CI exercises it hermetically.  The
    XML side is the trusted :func:`parse_he_branch`; the PDF side segments enacting-clause
    spans out of ``reading_text`` and lowers them through the IDENTICAL
    ``_parse_one_clause``.  Typed benign/deferred strata are returned as a status, never
    raised.
    """
    hid = he_id or f"HE {he_number}/{he_year} vp"
    branch = parse_he_branch(xml_bytes, he_year=he_year, he_number=he_number, he_id=hid)
    branch_id = branch.branch_id
    xml_flat = flatten_branch_ops(branch.proposed_ops)

    if not xml_flat:
        status, detail = _classify_xml_empty(xml_bytes, branch)
        return HECompareResult(hid, branch_id, status, (), 0, 0, detail)

    # PDF witness: segment enacting clauses and lower them through the SAME parser.
    pdf_ops: list = []
    for span in extract_enacting_clause_spans(reading_text):
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

    matched_refs = {d.target_ref for d in divergences if d.kind == _BENIGN_MATCH}
    matched_ops = tuple(op for op in xml_flat if op.target_ref in matched_refs)
    xml_bodies = _xml_proposed_bodies(xml_bytes)
    pdf_bodies = _pdf_proposed_bodies(reading_text)
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
    import pypdfium2 as pdfium

    from lawvm.ingest.visual import PDFIUM_LOCK

    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(data)
        try:
            n = min(len(pdf), max_pages)
            return "\n".join(pdf[i].get_textpage().get_text_range() for i in range(n))
        finally:
            pdf.close()


def he_pdf_reading_text(
    farchive: str, pdf_locator: str, *, max_pages: int = 400
) -> str:
    """Reconstruct an HE main.pdf's reading text (born-digital, zero image tokens).

    Reads ALL pages up to ``max_pages`` because the lakiehdotus (bill text) sits near the
    END of the HE, after the perustelut.  PRIMARY lane is the native pdfium TEXT LAYER
    (correct reading order for born-digital HEs); it falls back to the geom bbox
    reconstruction only when the text layer is absent/sparse (a scanned HE).
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
    max_pages: int = 400,
) -> HECompareResult:
    """Read both HE witnesses from the farchive and run :func:`compare_he`.

    XML from ``.../fin@/main.xml``; PDF reading text from ``.../fin@/main.pdf`` via the
    free geom lane.  A missing/unreadable witness surfaces as an ``error`` status.
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
        xml_bytes, reading_text, he_year=he_year, he_number=he_number, he_id=he_id
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
        f"payload_mismatch={c['payload_mismatch']}"
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
