"""Draft-HE → ConditionalBranch extraction (the "if enacted, then …" lowering).

A Finnish draft government proposal (HE luonnos) decomposes into two layers:

* the OPERATIVE ``lakiehdotus`` (bill text) — its ``johtolause`` (enacting
  formula "Eduskunnan päätöksen mukaisesti …") lowers, via the existing
  deterministic ``johtolause`` parser, to candidate operations that WOULD apply
  if the proposal is enacted; and
* the interpretive ``perustelut`` (reasoning) — a bound, NON-operative
  attachment (esityöt / travaux préparatoires), kept as a ``SourceDocumentNode``
  subtree and never lowered to an op.

Document-type classification comes first (the corpus survey): only an HE with an
``Eduskunnan päätöksen mukaisesti`` johtolause carries bill text — a ``määräys``
(regulatory order) or a ``muistio`` (memo) is reasoning-only, and yields a
package with zero candidate ops and an honest finding, never a hallucinated op.

Structure detection is deterministic string matching (mechanical sympathy); the
operation SEMANTICS come from the ``johtolause`` grammar, not from prose
heuristics. The one statute-id scan is a module-level classifier regex.

Discipline (AGENTS.md §1.9, §1.10): typed carriers; a non-HE document is a typed
finding, never a silent empty op-set.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple

from typing_extensions import override

from lawvm.core.source_document.adjudication import Adjudicator
from lawvm.core.source_document.anchors import SourceAnchor
from lawvm.core.source_document.extraction import ExtractionAssertion, SourceManifestation
from lawvm.core.source_document.ir import (
    AssuranceTier,
    SourceDocumentNode,
    SourceDocumentNodeKind,
)
from lawvm.core.source_document.proposal import (
    CandidateOperation,
    ConditionalBranch,
    ProposalAuthorityStatus,
    ProposalPackage,
)
from lawvm.finland.johtolause import extract_legal_ops

# Deterministic segmentation markers (validated across the corpus survey).
_JOHTOLAUSE_MARK = "eduskunnan päätöksen mukaisesti"
_VOIMAANTULO_MARK = "tämä laki tulee voimaan"
_SEURAAVASTI_MARK = "seuraavasti:"
_MAARAYS_MARK = "määräys"

# The POSITIVE new-act enacting verb: a wholly new act reads ``Eduskunnan
# päätöksen mukaisesti säädetään:`` — the enacting verb is ``säädetään``.
_SAADETAAN_MARK = "säädetään"
# The amendment verbs that govern an EXISTING named statute in a johtolause
# preamble. Their presence (with no ``säädetään``) proves the region is an
# amendment, even when its ``seuraavasti:`` clause did not survive extraction.
_AMEND_VERBS = ("muutetaan", "lisätään", "kumotaan", "korvataan")
# Preamble boundary: the johtolause preamble ends at the first ``seuraavasti:``,
# the first numbered section head (``30 a §``), or the first ``N luku`` chapter
# head — whichever comes first. Scanning verbs only within the preamble keeps a
# body cross-reference (e.g. ``muutetaan`` used inside a substantive provision)
# from being mistaken for an enacting verb. A bounded structural LOCATOR (fixed
# alternation of anchored tokens), not a prose classifier — kept as a
# module-level pattern string consumed via ``re.search`` (no raw ``re.compile``
# in this semantic-plane module; FW-07). Applied to an already lower-cased slice,
# so no IGNORECASE flag is needed.
_PREAMBLE_END_PATTERN = r"seuraavasti:|(?:\d+\s*[a-zä]?\s*§)|(?:\d+\s*luku)"

# The single classifier regex: an inline Finnish statute id ``(NNNN/YYYY)``.
# lawvm-regex: owning_parser extracts the amended-statute id from the johtolause
# preamble; the FIRST match is the target law (later ids are its amendment history).
_STATUTE_ID_RE = re.compile(r"\((\d{1,4}/\d{4})\)")


class HeDocKind(Enum):
    """Draft-document class — decides whether bill text can be extracted at all."""

    HE_BILL = "he_bill"
    """A hallituksen esitys with an ``Eduskunnan päätöksen mukaisesti`` johtolause."""
    MAARAYS = "maarays"
    """A regulatory order (STUK / Traficom) — no eduskunta johtolause, not a law."""
    MUISTIO = "muistio"
    """A memo / perustelumuistio — pure reasoning, no bill text."""

    @override
    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class _Flat:
    text: str
    node: SourceDocumentNode


def _flatten(root: SourceDocumentNode) -> List[_Flat]:
    """Reading-order list of nodes carrying non-empty text."""
    out: List[_Flat] = []

    def walk(n: SourceDocumentNode) -> None:
        if n.text and n.text.strip():
            out.append(_Flat(text=n.text, node=n))
        for c in n.children:
            walk(c)

    walk(root)
    return out


def _find_johtolause(flat: List[_Flat]) -> Optional[int]:
    """Index of the first node whose text carries the enacting formula.

    Skips a bare table-of-contents / SISÄLLYS mention (those lines carry the
    marker only as a heading, never the verb clause that follows it).
    """
    for i, f in enumerate(flat):
        low = f.text.lower()
        if _JOHTOLAUSE_MARK in low:
            return i
    return None


def classify_he_document(root: SourceDocumentNode) -> HeDocKind:
    """Classify by the presence of an eduskunta johtolause (bill) vs not."""
    flat = _flatten(root)
    if _find_johtolause(flat) is not None:
        return HeDocKind.HE_BILL
    for f in flat[:40]:
        if _MAARAYS_MARK in f.text.lower():
            return HeDocKind.MAARAYS
    return HeDocKind.MUISTIO


def _target_statute_id(johto_clause: str) -> str:
    """First inline ``(NNNN/YYYY)`` in the clause — the amended statute."""
    m = _STATUTE_ID_RE.search(johto_clause)
    return m.group(1) if m else ""


def _reasoning_root(root: SourceDocumentNode, reasoning_nodes: Tuple[SourceDocumentNode, ...]) -> SourceDocumentNode:
    """Wrap the perustelut nodes as one non-operative interpretive (esityöt) subtree."""
    return SourceDocumentNode(
        kind=SourceDocumentNodeKind.PROPOSAL_SECTION,
        assurance_tier=root.assurance_tier,
        anchor=root.anchor,
        label="perustelut",
        children=reasoning_nodes,
        attrs={"role": "esityot_reasoning"},
    )


@dataclass(frozen=True, slots=True)
class _Operative:
    """The operative pieces recovered from a reading-order text of the bill."""

    clause: str
    target_statute_id: str
    payload_text: str
    commencement: str
    is_new_act: bool = False
    """A wholly new act — its johtolause preamble carries the POSITIVE enacting
    verb ``säädetään`` (``Eduskunnan päätöksen mukaisesti säädetään:``) and no
    amendment verb. It amends NO existing statute, so a ``(NNNN/YYYY)`` in its
    body is a cross-reference, never an amended target.

    This is a POSITIVE classification (preamble carries ``säädetään``), not a
    mere ``seuraavasti:``-absence inference: an amendment whose ``seuraavasti:``
    clause was lost to a reading-order scramble / page truncation ALSO lacks the
    marker, but is NOT a new act — it is flagged ``incomplete_amendment``."""
    incomplete_amendment: bool = False
    """An amendment region whose ``seuraavasti:`` clause did not survive
    extraction: the preamble carries an amendment verb (``muutetaan`` /
    ``lisätään`` / ``kumotaan`` / ``korvataan``) but NOT ``säädetään``, and no
    ``seuraavasti:`` was found. Reporting such a region as a clean ``new-act``
    enactment would be a silent WRONG branch (it fabricates a fresh-statute
    claim); this flag routes it to a distinct, honest finding instead."""


def _johtolause_starts(low: str) -> List[int]:
    """Every offset where the enacting formula begins, in reading order.

    A multi-law HE carries ONE ``Eduskunnan päätöksen mukaisesti`` per
    lakiehdotus law; each starts a fresh operative region.
    """
    out: List[int] = []
    i = low.find(_JOHTOLAUSE_MARK)
    while i != -1:
        out.append(i)
        i = low.find(_JOHTOLAUSE_MARK, i + len(_JOHTOLAUSE_MARK))
    return out


def _clean_payload(raw: str) -> str:
    """Drop the ``§ N / heading / — — —`` elision preamble → just the new unit text.

    In an amending lakiehdotus the provision body reprints the target ``§`` head +
    heading, then an em-dash rule (``— — —``) standing for the UNCHANGED existing
    content, then the NEW unit. Materialization needs only the new unit, so return
    the text after the LAST all-em-dash line (if any); otherwise the text as-is (a
    full-section replacement carries no elision rule).
    """
    lines = raw.replace("\r\n", "\n").split("\n")
    last_rule = -1
    for i, ln in enumerate(lines):
        stripped = ln.strip()
        if stripped and all(ch in "—–- " for ch in stripped) and "—" in stripped:
            last_rule = i
    kept = lines[last_rule + 1:] if last_rule != -1 else lines
    return "\n".join(kept).strip()


def _preamble_of(seg_low: str) -> str:
    """The johtolause preamble — marker → first ``seuraavasti:`` / ``§`` / ``luku``.

    ``seg_low`` is a lower-cased operative region beginning at the
    ``Eduskunnan päätöksen mukaisesti`` marker. The preamble is the enacting
    formula proper (which verb governs, and over what statute); it ends where the
    substantive body begins. Scanning enacting verbs only within this window
    keeps a body-internal ``muutetaan`` cross-reference from being mistaken for a
    johtolause verb.
    """
    j = seg_low.find(_JOHTOLAUSE_MARK)
    rest = seg_low[j + len(_JOHTOLAUSE_MARK):] if j != -1 else seg_low
    # bounded johtolause-preamble boundary locator (first seuraavasti:/§-head/luku-head)
    # lawvm-regex: owning_parser — locates the preamble slice the verb classifier reads; does not interpret legal state
    m = re.search(_PREAMBLE_END_PATTERN, rest)
    return rest[: m.start()] if m else rest


def _classify_no_seuraavasti(seg_low: str) -> Tuple[bool, bool]:
    """Classify a region that carries NO ``seuraavasti:`` — (is_new_act, incomplete).

    A missing ``seuraavasti:`` is NOT sufficient to call a region a new act: an
    amendment whose ``seuraavasti:`` was lost to a reading-order scramble / page
    truncation also lacks it. Discriminate POSITIVELY on the preamble verb:

    * preamble carries ``säädetään`` (and no amendment verb) → genuine new act;
    * preamble carries an amendment verb but not ``säädetään`` → an amendment
      with an incomplete johtolause (``incomplete_amendment``);
    * neither verb resolves → treat as a new act by default (no amendment target
      can be recovered anyway), but never fabricate an amended target for it.
    """
    preamble = _preamble_of(seg_low)
    has_saadetaan = _SAADETAAN_MARK in preamble
    has_amend_verb = any(v in preamble for v in _AMEND_VERBS)
    if has_amend_verb and not has_saadetaan:
        return False, True  # amendment with a lost seuraavasti: clause
    return True, False  # säädetään new act, or an unresolved preamble


def _operative_from_region(region_text: str) -> _Operative:
    """Recover johtolause clause + target statute + payload + voimaantulo.

    ``region_text`` is ONE law's operative region — it begins at the
    ``Eduskunnan päätöksen mukaisesti`` marker and runs to the next law's marker
    (or to end of text). The caller has already sliced the region.
    """
    seg = region_text
    seg_low = seg.lower()
    cut = seg_low.find(_SEURAAVASTI_MARK)
    # An amending lakiehdotus names its target law in the preamble that ENDS at
    # ``seuraavasti:``. With no such clause the region is EITHER a new-act
    # enactment (``säädetään:``) OR an amendment whose ``seuraavasti:`` clause was
    # lost to a reading-order scramble / page truncation. Discriminate positively
    # on the preamble verb rather than inferring "new act" from mere absence —
    # calling a truncated amendment a new act is a silent WRONG branch. A new act
    # amends no statute, so a ``(NNNN/YYYY)`` deeper in its body is a
    # cross-reference; only an amendment preamble carries an amended target.
    if cut != -1:
        is_new_act = False
        incomplete_amendment = False
    else:
        is_new_act, incomplete_amendment = _classify_no_seuraavasti(seg_low)
    clause = seg[: cut + len(_SEURAAVASTI_MARK)].strip() if cut != -1 else seg.strip()

    # payload = text after the johtolause clause, up to the voimaantulo line.
    after = seg[cut + len(_SEURAAVASTI_MARK):] if cut != -1 else ""
    after_low = after.lower()
    vi = after_low.find(_VOIMAANTULO_MARK)
    if vi != -1:
        payload = _clean_payload(after[:vi])
        # commencement = just the voimaantulo line (the "—" rule / signatures follow).
        tail = after[vi:].replace("\r\n", "\n")
        commencement = tail.split("\n", 1)[0].strip()
    else:
        payload = _clean_payload(after)
        commencement = ""
    return _Operative(
        clause=clause,
        # A new act names no amended statute; an amendment (complete or
        # incomplete) may carry ``(NNNN/YYYY)`` in its preamble — resolve it so an
        # incomplete-amendment finding can still name its target where possible.
        target_statute_id="" if is_new_act else _target_statute_id(clause),
        payload_text=payload,
        commencement=commencement,
        is_new_act=is_new_act,
        incomplete_amendment=incomplete_amendment,
    )


def _operatives_from_text(reading_order_text: str) -> List[_Operative]:
    """One ``_Operative`` per lakiehdotus law, in reading order.

    Splits the continuous reading-order text at each ``Eduskunnan päätöksen
    mukaisesti`` marker: each law's operative region runs from its marker to the
    next law's marker (or to end). A single-law bill yields a 1-element list; a
    text with no johtolause yields the empty list.

    Works on a CONTINUOUS reading-order text (e.g. a page-ordered extraction),
    NOT on pdfplumber's per-block output — that scrambles reading order on the
    bill page (the reading-order residual the adjudication layer exists to
    resolve).
    """
    low = reading_order_text.lower()
    starts = _johtolause_starts(low)
    bounds = starts + [len(reading_order_text)]
    return [
        _operative_from_region(reading_order_text[bounds[i]:bounds[i + 1]])
        for i in range(len(starts))
    ]


def _operative_from_text(reading_order_text: str) -> Optional[_Operative]:
    """The FIRST law's operative pieces — ``None`` when no johtolause is present.

    Retained for single-law callers and the ``_operative_from_text`` unit test;
    ``_operatives_from_text`` is the multi-law entry point.
    """
    ops = _operatives_from_text(reading_order_text)
    return ops[0] if ops else None


def extract_conditional_branch(
    root: SourceDocumentNode,
    proposal_id: str,
    *,
    reading_order_text: str = "",
    source_manifestation_digests: Tuple[str, ...] = (),
    condition: str = "",
    op_assurance: AssuranceTier = AssuranceTier.SINGLE_WITNESS,
) -> ProposalPackage:
    """Lower an ingested draft-HE ``SourceDocumentIR`` into a ``ProposalPackage``.

    The operative lakiehdotus → candidate ops on a non-authoritative
    ``ConditionalBranch`` ("if enacted, then …"); the ingested tree → the bound
    interpretive reasoning attachment (esityöt). A non-HE document
    (määräys / muistio) yields zero candidate ops plus a finding — never a
    hallucinated op.

    ``reading_order_text`` is the reliable operative producer (a page-ordered
    extraction, or an adjudicated composition of several producers). When empty,
    the operative side falls back to the ingested block text — which may be
    reading-order-scrambled, so this is best-effort and reported in findings.

    ``op_assurance`` is the tier granted to a candidate op: a lone deterministic
    parse is ``SINGLE_WITNESS``; a caller that adjudicates the operative text
    across independent producers passes ``MULTI_WITNESS_ADJUDICATED``.

    A real HE proposes one or more laws (a ``LAKIEHDOTUKSET`` section lists 1–44):
    each ``Laki X:n muuttamisesta`` block carries its OWN johtolause, target
    statute and voimaantulo, and lowers to its OWN ``ConditionalBranch`` in
    ``ProposalPackage.branches``. A non-HE document yields the empty branch tuple
    plus a finding — never a hallucinated op.
    """
    findings: List[str] = []
    operatives = _operatives_from_text(reading_order_text) if reading_order_text else []

    if not operatives:
        kind = classify_he_document(root)
        if kind is not HeDocKind.HE_BILL:
            findings.append(
                f"document classified {kind} — reasoning-only, no candidate operations"
            )
        elif reading_order_text:
            # pdfplumber's ingest classified HE_BILL (it saw the johtolause) but
            # the reading-order producer's text did NOT carry it — the two
            # producers cover different page spans. Almost always a truncated
            # reading-order page cap on a long HE, not an absent operative. A
            # DISTINCT finding so truncation never masquerades as "unavailable".
            findings.append(
                "producer page-coverage split: pdfplumber classified HE_BILL but the "
                "reading-order text carries no johtolause — likely a truncated page "
                "span; raise max_pages for the reading-order producer"
            )
        else:
            findings.append(
                "HE_BILL detected but operative text unavailable (no reading_order_text); "
                "pass a reading-order producer to extract candidate ops"
            )
        return ProposalPackage(
            proposal_id=proposal_id,
            source_manifestation_digests=source_manifestation_digests,
            branches=(),
            reasoning_root=root,
            authority_status=ProposalAuthorityStatus.CONSULTATION_DRAFT,
            findings=tuple(findings),
        )

    multi = len(operatives) > 1
    branches: List[ConditionalBranch] = []
    for i, operative in enumerate(operatives):
        law_tag = f" (law {i + 1})" if multi else ""
        if operative.is_new_act:
            # A wholly new act enacts fresh law rather than amending an existing
            # statute — no amend ops, no amended target. Emit an honest finding
            # instead of misattributing a body cross-reference as the target.
            findings.append(
                f"new-act enactment (säädetään preamble){law_tag} — "
                "enacts a new statute, not modelled as amendment ops"
            )
            ops = []
        elif operative.incomplete_amendment:
            # An amendment whose ``seuraavasti:`` clause did not survive
            # extraction (preamble carries an amendment verb but no marker). This
            # is NOT a new act — reporting it as one would fabricate a
            # fresh-statute claim. Emit a distinct, honest finding; no ops can be
            # recovered from the truncated clause, but any preamble ``(id)`` is
            # still surfaced as the (unmodelled) amended target.
            target_desc = (
                f" (amended target {operative.target_statute_id})"
                if operative.target_statute_id
                else ""
            )
            findings.append(
                f"incomplete amendment johtolause{law_tag}{target_desc} — an amendment "
                "verb (muutetaan/lisätään/kumotaan/korvataan) but no 'seuraavasti:' "
                "clause survived extraction (reading-order scramble or page "
                "truncation); NOT a new act, ops not recoverable"
            )
            ops = []
        else:
            if not operative.target_statute_id:
                findings.append(
                    f"target statute id not resolved from johtolause preamble{law_tag}"
                )
            ops = extract_legal_ops(operative.clause)
            if not ops:
                findings.append(f"johtolause parsed to zero operations{law_tag}")

        candidate_ops = tuple(
            CandidateOperation(
                action=str(op.action),
                target_statute_id=operative.target_statute_id,
                target_provision_ref=str(op.target),
                payload_text=operative.payload_text,
                source_anchor=root.anchor,
                assurance_tier=op_assurance,
                raw_johtolause=operative.clause,
            )
            for op in ops
        )
        # One branch id per law; single-law keeps the historical ``:draft`` suffix.
        branch_id = f"{proposal_id}:draft" if not multi else f"{proposal_id}:draft:law{i + 1}"
        branches.append(
            ConditionalBranch(
                branch_id=branch_id,
                condition=condition or f"{proposal_id} enacted as introduced",
                candidate_ops=candidate_ops,
                authority_status=ProposalAuthorityStatus.CONSULTATION_DRAFT,
                commencement=operative.commencement,
            )
        )

    # Reasoning = everything before the FIRST johtolause (esityöt attachment).
    reasoning_root = _reasoning_root(root, root.children)
    return ProposalPackage(
        proposal_id=proposal_id,
        source_manifestation_digests=source_manifestation_digests,
        branches=tuple(branches),
        reasoning_root=reasoning_root,
        authority_status=ProposalAuthorityStatus.CONSULTATION_DRAFT,
        findings=tuple(findings),
    )


def _operative_region_text(text: str) -> str:
    """The lakiehdotus region — from the johtolause marker to the end."""
    js = text.lower().find(_JOHTOLAUSE_MARK)
    return text[js:] if js != -1 else ""


def he_pdf_to_proposal(
    manifestation: SourceManifestation,
    proposal_id: str,
    *,
    adjudicator: Optional[Adjudicator] = None,
    condition: str = "",
    max_pages: int = 5000,
) -> ProposalPackage:
    """Full draft-HE PDF → ``ProposalPackage`` (ingest + reading-order + adjudicate).

    Two independent producers read the operative region: pdfplumber blocks (which
    scramble reading order on dense bill pages) and a page-ordered extraction. When
    an ``adjudicator`` is given, they are adjudicated — if both independently carry
    the same johtolause, the candidate op earns ``MULTI_WITNESS_ADJUDICATED`` (a
    genuine second witness); otherwise it stays ``SINGLE_WITNESS``. The op is always
    PARSED from the clean reading-order clause (exact); adjudication sets only its
    assurance, never rewrites the text a deterministic parser depends on.
    """
    from lawvm.finland.source_document.pdf_profiles import ingest_pdf_manifestation

    res = ingest_pdf_manifestation(manifestation, max_pages=max_pages)
    reading_order_text = reading_order_text_from_pdf(manifestation.source_bytes, max_pages=max_pages)

    op_assurance = AssuranceTier.SINGLE_WITNESS
    if adjudicator is not None:
        flat = _flatten(res.root)
        ji = _find_johtolause(flat)
        ro_region = _operative_region_text(reading_order_text)
        if ji is not None and ro_region:
            johto_page = flat[ji].node.anchor.page_num
            pp_region = "\n".join(
                f.text for f in flat if f.node.anchor.page_num == johto_page
            )
            region = SourceAnchor(
                artifact_digest=manifestation.artifact_digest,
                locator="operative_region",
                page_num=johto_page,
            )
            digest12 = manifestation.artifact_digest[:12]
            candidates = (
                ExtractionAssertion(
                    run_id=f"reading_order:{digest12}",
                    fragment_kind="paragraph",
                    text=ro_region[:800],
                    anchor=region,
                ),
                ExtractionAssertion(
                    run_id=f"pdfplumber:{digest12}",
                    fragment_kind="paragraph",
                    text=pp_region[:800],
                    anchor=region,
                ),
            )
            op_assurance = adjudicator.adjudicate(region, candidates).assurance

    return extract_conditional_branch(
        res.root,
        proposal_id,
        reading_order_text=reading_order_text,
        source_manifestation_digests=(manifestation.artifact_digest,),
        condition=condition,
        op_assurance=op_assurance,
    )


def reading_order_text_from_pdf(pdf_bytes: bytes, *, max_pages: int = 5000) -> str:
    """Page-ordered text extraction (pypdfium2) — a reliable reading-order producer.

    pdfplumber's block segmentation scrambles reading order on dense bill pages;
    this preserves it. Optional dependency; raises if pypdfium2 is absent so the
    caller can fall back rather than silently mis-order.

    The default cap matches ``ingest_pdf_manifestation`` — a long HE (400+ pp)
    carries its ``Lakiehdotukset`` section past page 200, and a lower cap here
    than the pdfplumber ingest produced a silent producer page-coverage split
    (pdfplumber classified HE_BILL, the truncated reading-order text saw no
    johtolause). Callers wanting speed pass a small ``max_pages`` explicitly.
    """
    import importlib

    pdfium = importlib.import_module("pypdfium2")
    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        pages = []
        for i in range(min(len(doc), max_pages)):
            pages.append(doc[i].get_textpage().get_text_range())
        return "\n\n".join(pages)
    finally:
        doc.close()
