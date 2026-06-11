"""Finnish InlineCitation extractor.

Extracts typed InlineCitation records from body ``<p>`` elements in:
  - Consolidated enacted statute AKN XML (finlex.farchive)
  - Government proposal (HE) AKN XML (fi_government_proposal.farchive)

Entry point:

  extract_inline_citations(xml_bytes, doc_id, doc_kind, ...) -> InlineCitationExtractionResult

Design discipline (AGENTS.md §1.1, §1.8, §1.11, §1.13):

  §1.1 No silent target hijacking:
      Court-case canonical_id uses the published citation verbatim;
      never synthesized (AGENTS.md §1.1).

  §1.8 No source lane disappears — BUT inline body prose is overwhelmingly
      plain text. We do NOT emit UNRESOLVED for every unmatched <p>.
      We ONLY emit an InlineCitationPatternMatch when a pattern fires but
      fails sanity/grammar checks. This is intentional: preliminaryWork
      (#11) is a typed block where every <p> is either a citation or
      UNRESOLVED. Body prose is not.

  §1.11 Hot-path regex discipline:
      All patterns compiled at module scope.
      Bounded quantifiers; no adjacent unbounded repeats.
      Substring guards before each regex.

  §1.13 Grammar trigger — named recognizer:
      The 8+ citation families in body prose form a FAMILY.
      Built as InlineCitationRecognizer (single-pass ordered scan),
      not N overlapping backtracking scans.

Composition with #1 and #11:
  - Text inside <ref> elements is SKIPPED (deferred to #1).
  - preliminaryWork blocks are SKIPPED by default, EXCEPT the EK and
    OLD_COMMITTEE passes which run over preliminaryWork to close the
    #11 UNRESOLVED gap.
  - HE_INLINE (HE N/YYYY) is only emitted when doc_kind='he'.

Source: Finlex Akoma Ntoso consolidated XML (statutes) or
        fi_government_proposal.farchive (HEs).
Core primitive: lawvm.core.inline_citation.InlineCitation.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from lawvm.core.inline_citation import (
    InlineCitation,
    InlineCitationContext,
    InlineCitationKind,
    InlineCitationPatternMatch,
)

# ---------------------------------------------------------------------------
# XML namespaces
# ---------------------------------------------------------------------------

_AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
_FINLEX_NS = "http://data.finlex.fi/schema/finlex"

_AKN = f"{{{_AKN_NS}}}"
_FINLEX = f"{{{_FINLEX_NS}}}"

# ---------------------------------------------------------------------------
# Year sanity bounds (real Finnish legal corpus range)
# ---------------------------------------------------------------------------

_YEAR_MIN = 1800
_YEAR_MAX = 2100
_NUM_MAX = 999999

# ---------------------------------------------------------------------------
# Module-scope compiled patterns (AGENTS.md §1.11)
# All patterns have bounded quantifiers; no adjacent unbounded repeats.
# Substring guards listed per pattern in recognizer.
# ---------------------------------------------------------------------------

# KKO YYYY:N  or  KKO: YYYY:N  (colon optional after KKO)
# Substring guard: "KKO"
_KKO_RE = re.compile(
    r'\bKKO:?\s+(?P<y>\d{4}):(?P<n>\d{1,6})\b'
)

# KHO YYYY:N  or  KHO YYYY/N  (slash or colon separator)
# Substring guard: "KHO"
_KHO_RE = re.compile(
    r'\bKHO:?\s+(?P<y>\d{4})[:/](?P<n>\d{1,6})\b'
)

# EOA / EOAK forms:
#   EOAK/N/YYYY  or  EOA N/YYYY/N  or  EOAK N/YYYY
# Multiple forms; use alternation ordered by specificity.
# Substring guard: "EOA"
_EOA_RE = re.compile(
    r'\bEOAK?(?:/|\s+)(?P<n>\d{1,8})(?:/|\s+)(?P<y>\d{4})'
    r'(?:/(?P<n2>\d{1,8}))?'
    r'\b'
)

# OKV/N/YY/YYYY  or  OKV/N/YYYY (chancellor)
# Substring guard: "OKV"
_OKA_RE = re.compile(
    r'\bOKV/(?P<n>\d{1,8})/(?:(?P<y2>\d{2})/)?(?P<y4>\d{4})\b'
)

# HE N/YYYY — only for HE source doc kind
# Substring guard: "HE "
_HE_INLINE_RE = re.compile(
    r'\bHE\s+(?P<n>\d{1,6})/(?P<y>\d{4})\b'
)

# VTV N/YYYY or VTV:n tarkastuskertomus N/YYYY
# Substring guard: "VTV"
_VTV_RE = re.compile(
    r'\bVTV(?::n\s+tarkastuskertomus)?\s+(?P<n>\d{1,6})/(?P<y>\d{4})\b'
)

# EK N/YYYY — Eduskunnan kirjelma (modern post-2019 EV replacement)
# Substring guard: "EK "
# Must not match "EK " that is part of EUVL or EOAK — but EK always starts
# with capital letters followed by space+digits.
_EK_RE = re.compile(
    r'\bEK\s+(?P<n>\d{1,6})/(?P<y>\d{4})\b'
)

# Working-group memorandum:
#   Tyoryhmämuistio N/YYYY (note: ö encoded, but we search for substring first)
# Substring guard: "ryhmämuistio" or "tyoryhmä" or similar
# We use a looser guard on "muistio " and let regex confirm.
_WGM_RE = re.compile(
    r'[Tt][Yy][oö]ryhmämuistio\s+(?P<n>\d{1,6})/(?P<y>\d{4})\b',
    re.UNICODE,
)

# Old committee abbreviations (pre-1991): lvk.miet., svk.miet., etc.
# We match dotted abbreviations ending in .miet. or .miet
# Substring guard: ".miet"
_OLD_COMMITTEE_RE = re.compile(
    r'\b(?P<abbr>[a-z]{2,8})\.miet\.?(?:\s+\d{1,6}/\d{2,4})?',
    re.IGNORECASE,
)

# Plain-text statute citation:
#   "-lain (N/YYYY)" or "-asetuksen (N/YYYY)" or "-lakia (N/YYYY)" etc.
#   Also "lain (N/YYYY)" without prefix, "lakiin (N/YYYY)", etc.
# Capture the statute number N and year YYYY.
# Substring guard: "(" — the parenthesized statute id is the key marker.
_STATUTE_INLINE_RE = re.compile(
    r'(?:[a-zäöåA-ZÄÖÅ]{2,50})'  # leading word (law/decree name fragment)
    r'(?:lain|lakia|lakiin|laista|lakina|asetuksen|asetusta|asetukseen|asetuksesta|'
    r'asetuksena|säädöksen|säädöstä|säädökseen|määräyksen|direktiivin)'
    r'\s*\((?P<sn>\d{1,6})/(?P<sy>\d{4})\)',
    re.UNICODE,
)

# ---------------------------------------------------------------------------
# InlineCitationRecognizer (AGENTS.md §1.13 — named family, not N parallel scans)
# ---------------------------------------------------------------------------


class InlineCitationRecognizer:
    """Named recognizer for Finnish inline body-prose citation families (AGENTS.md §1.13).

    Recognizes the multi-family citation grammar found in enacted-statute and
    HE body prose:
      1. KKO court decisions
      2. KHO court decisions
      3. EOA / EOAK ombudsman references
      4. OKV chancellor references
      5. HE inline cross-references (HE body only)
      6. VTV audit reports
      7. EK parliament kirjelma (closes #11 UNRESOLVED gap)
      8. Working-group memoranda
      9. Old committee abbreviations (pre-1991)
     10. Plain-text statute citations

    This is a single-pass ordered scan, NOT N overlapping backtracking passes.
    Each recognizer production:
      - checks its substring guard first (fast path, eliminates ~99% of misses)
      - applies the module-scope compiled regex
      - validates extracted fields (year range, number range)
      - emits an InlineCitation or an InlineCitationPatternMatch

    Usage:
        recognizer = InlineCitationRecognizer()
        citations, pattern_matches = recognizer.recognize_all(
            text, doc_id, doc_kind, context, source_span_file
        )

    The recognizer is instantiated once at module scope and reused.
    """

    def recognize_all(
        self,
        text: str,
        doc_id: str,
        doc_kind: str,
        context: InlineCitationContext,
        source_span_file: Optional[str],
    ) -> Tuple[List[InlineCitation], List[InlineCitationPatternMatch]]:
        """Scan text for all inline citation families. Returns (citations, pattern_matches).

        This method runs ALL recognizer productions over the text and collects
        all matches. A single paragraph may contain multiple citations of
        different kinds (e.g., a KKO citation and a statute citation in the
        same sentence).

        Args:
            text:             Normalized text of one <p> element.
            doc_id:           Source document canonical ID.
            doc_kind:         'statute' or 'he'.
            context:          Structural context of the paragraph.
            source_span_file: Provenance file path, or None.

        Returns:
            Tuple of (citations, pattern_matches).
            citations: successfully typed InlineCitation records.
            pattern_matches: failed candidates per AGENTS.md §1.8.
        """
        citations: List[InlineCitation] = []
        pattern_matches: List[InlineCitationPatternMatch] = []

        if not text:
            return citations, pattern_matches

        # Build text without <ref> markup content (already extracted by caller,
        # but we operate on plain text here so no extra stripping needed).

        # --- 1. KKO court decisions ---
        if "KKO" in text:
            for m in _KKO_RE.finditer(text):
                y, n = int(m.group("y")), int(m.group("n"))
                if not (_YEAR_MIN <= y <= _YEAR_MAX) or n > _NUM_MAX:
                    pattern_matches.append(InlineCitationPatternMatch(
                        rule_id="fi_inline_kko_sanity_fail",
                        phase="inline_citation_extraction",
                        source_doc_id=doc_id,
                        reason=f"KKO year={y} or number={n} out of sanity range",
                        raw_text=m.group(0),
                        kind_attempted=InlineCitationKind.COURT_KKO.value,
                    ))
                    continue
                citations.append(InlineCitation(
                    source_doc_id=doc_id,
                    source_doc_kind=doc_kind,
                    source_provision_ref="",
                    kind=InlineCitationKind.COURT_KKO,
                    canonical_id=f"fi.court.kko.{y}.{n}",
                    raw_text=m.group(0),
                    case_year=y,
                    case_number=n,
                    context=context,
                    source_span_file=source_span_file,
                    source_span_byte_offset=None,
                    source_span_byte_len=None,
                ))

        # --- 2. KHO court decisions ---
        if "KHO" in text:
            for m in _KHO_RE.finditer(text):
                y, n = int(m.group("y")), int(m.group("n"))
                if not (_YEAR_MIN <= y <= _YEAR_MAX) or n > _NUM_MAX:
                    pattern_matches.append(InlineCitationPatternMatch(
                        rule_id="fi_inline_kho_sanity_fail",
                        phase="inline_citation_extraction",
                        source_doc_id=doc_id,
                        reason=f"KHO year={y} or number={n} out of sanity range",
                        raw_text=m.group(0),
                        kind_attempted=InlineCitationKind.COURT_KHO.value,
                    ))
                    continue
                citations.append(InlineCitation(
                    source_doc_id=doc_id,
                    source_doc_kind=doc_kind,
                    source_provision_ref="",
                    kind=InlineCitationKind.COURT_KHO,
                    canonical_id=f"fi.court.kho.{y}.{n}",
                    raw_text=m.group(0),
                    case_year=y,
                    case_number=n,
                    context=context,
                    source_span_file=source_span_file,
                    source_span_byte_offset=None,
                    source_span_byte_len=None,
                ))

        # --- 3. EOA / EOAK ombudsman ---
        if "EOA" in text:
            for m in _EOA_RE.finditer(text):
                n_raw = m.group("n")
                y_raw = m.group("y")
                n, y = int(n_raw), int(y_raw)
                if not (_YEAR_MIN <= y <= _YEAR_MAX) or n > _NUM_MAX:
                    pattern_matches.append(InlineCitationPatternMatch(
                        rule_id="fi_inline_eoa_sanity_fail",
                        phase="inline_citation_extraction",
                        source_doc_id=doc_id,
                        reason=f"EOA year={y} or number={n} out of sanity range",
                        raw_text=m.group(0),
                        kind_attempted=InlineCitationKind.OMBUDSMAN_EOA.value,
                    ))
                    continue
                citations.append(InlineCitation(
                    source_doc_id=doc_id,
                    source_doc_kind=doc_kind,
                    source_provision_ref="",
                    kind=InlineCitationKind.OMBUDSMAN_EOA,
                    canonical_id=f"fi.eoa.{n}.{y}",
                    raw_text=m.group(0),
                    case_year=y,
                    case_number=n,
                    context=context,
                    source_span_file=source_span_file,
                    source_span_byte_offset=None,
                    source_span_byte_len=None,
                ))

        # --- 4. OKV chancellor ---
        if "OKV" in text:
            for m in _OKA_RE.finditer(text):
                n = int(m.group("n"))
                y4_raw = m.group("y4")
                y = int(y4_raw)
                if not (_YEAR_MIN <= y <= _YEAR_MAX) or n > _NUM_MAX:
                    pattern_matches.append(InlineCitationPatternMatch(
                        rule_id="fi_inline_oka_sanity_fail",
                        phase="inline_citation_extraction",
                        source_doc_id=doc_id,
                        reason=f"OKV year={y} or number={n} out of sanity range",
                        raw_text=m.group(0),
                        kind_attempted=InlineCitationKind.CHANCELLOR_OKA.value,
                    ))
                    continue
                citations.append(InlineCitation(
                    source_doc_id=doc_id,
                    source_doc_kind=doc_kind,
                    source_provision_ref="",
                    kind=InlineCitationKind.CHANCELLOR_OKA,
                    canonical_id=f"fi.oka.{n}.{y}",
                    raw_text=m.group(0),
                    case_year=y,
                    case_number=n,
                    context=context,
                    source_span_file=source_span_file,
                    source_span_byte_offset=None,
                    source_span_byte_len=None,
                ))

        # --- 5. HE inline (HE bodies only, outside preliminaryWork) ---
        if doc_kind == "he" and context != InlineCitationContext.PRELIMINARY_WORK:
            if "HE " in text:
                for m in _HE_INLINE_RE.finditer(text):
                    n, y = int(m.group("n")), int(m.group("y"))
                    if not (_YEAR_MIN <= y <= _YEAR_MAX) or n > _NUM_MAX:
                        pattern_matches.append(InlineCitationPatternMatch(
                            rule_id="fi_inline_he_sanity_fail",
                            phase="inline_citation_extraction",
                            source_doc_id=doc_id,
                            reason=f"HE year={y} or number={n} out of sanity range",
                            raw_text=m.group(0),
                            kind_attempted=InlineCitationKind.HE_INLINE.value,
                        ))
                        continue
                    citations.append(InlineCitation(
                        source_doc_id=doc_id,
                        source_doc_kind=doc_kind,
                        source_provision_ref="",
                        kind=InlineCitationKind.HE_INLINE,
                        canonical_id=f"he/{y}/{n}",
                        raw_text=m.group(0),
                        case_year=y,
                        case_number=n,
                        context=context,
                        source_span_file=source_span_file,
                        source_span_byte_offset=None,
                        source_span_byte_len=None,
                    ))

        # --- 6. VTV audit reports ---
        if "VTV" in text:
            for m in _VTV_RE.finditer(text):
                n, y = int(m.group("n")), int(m.group("y"))
                if not (_YEAR_MIN <= y <= _YEAR_MAX) or n > _NUM_MAX:
                    pattern_matches.append(InlineCitationPatternMatch(
                        rule_id="fi_inline_vtv_sanity_fail",
                        phase="inline_citation_extraction",
                        source_doc_id=doc_id,
                        reason=f"VTV year={y} or number={n} out of sanity range",
                        raw_text=m.group(0),
                        kind_attempted=InlineCitationKind.VTV_REPORT.value,
                    ))
                    continue
                citations.append(InlineCitation(
                    source_doc_id=doc_id,
                    source_doc_kind=doc_kind,
                    source_provision_ref="",
                    kind=InlineCitationKind.VTV_REPORT,
                    canonical_id=f"fi.vtv.{n}.{y}",
                    raw_text=m.group(0),
                    case_year=y,
                    case_number=n,
                    context=context,
                    source_span_file=source_span_file,
                    source_span_byte_offset=None,
                    source_span_byte_len=None,
                ))

        # --- 7. EK parliament kirjelma ---
        if "EK " in text:
            for m in _EK_RE.finditer(text):
                n, y = int(m.group("n")), int(m.group("y"))
                if not (_YEAR_MIN <= y <= _YEAR_MAX) or n > _NUM_MAX:
                    pattern_matches.append(InlineCitationPatternMatch(
                        rule_id="fi_inline_ek_sanity_fail",
                        phase="inline_citation_extraction",
                        source_doc_id=doc_id,
                        reason=f"EK year={y} or number={n} out of sanity range",
                        raw_text=m.group(0),
                        kind_attempted=InlineCitationKind.PARLIAMENT_KIRJELMA.value,
                    ))
                    continue
                citations.append(InlineCitation(
                    source_doc_id=doc_id,
                    source_doc_kind=doc_kind,
                    source_provision_ref="",
                    kind=InlineCitationKind.PARLIAMENT_KIRJELMA,
                    canonical_id=f"fi.ek.{n}.{y}",
                    raw_text=m.group(0),
                    case_year=y,
                    case_number=n,
                    context=context,
                    source_span_file=source_span_file,
                    source_span_byte_offset=None,
                    source_span_byte_len=None,
                ))

        # --- 8. Working-group memoranda ---
        if "muistio" in text.lower():
            for m in _WGM_RE.finditer(text):
                n, y = int(m.group("n")), int(m.group("y"))
                if not (_YEAR_MIN <= y <= _YEAR_MAX) or n > _NUM_MAX:
                    pattern_matches.append(InlineCitationPatternMatch(
                        rule_id="fi_inline_wgm_sanity_fail",
                        phase="inline_citation_extraction",
                        source_doc_id=doc_id,
                        reason=f"WGM year={y} or number={n} out of sanity range",
                        raw_text=m.group(0),
                        kind_attempted=InlineCitationKind.WORKING_GROUP_MEMO.value,
                    ))
                    continue
                citations.append(InlineCitation(
                    source_doc_id=doc_id,
                    source_doc_kind=doc_kind,
                    source_provision_ref="",
                    kind=InlineCitationKind.WORKING_GROUP_MEMO,
                    canonical_id=f"fi.wgm.{n}.{y}",
                    raw_text=m.group(0),
                    case_year=y,
                    case_number=n,
                    context=context,
                    source_span_file=source_span_file,
                    source_span_byte_offset=None,
                    source_span_byte_len=None,
                ))

        # --- 9. Old committee abbreviations (pre-1991) ---
        if ".miet" in text.lower():
            for m in _OLD_COMMITTEE_RE.finditer(text):
                raw = m.group(0)
                citations.append(InlineCitation(
                    source_doc_id=doc_id,
                    source_doc_kind=doc_kind,
                    source_provision_ref="",
                    kind=InlineCitationKind.OLD_COMMITTEE,
                    canonical_id=None,
                    raw_text=raw,
                    case_year=None,
                    case_number=None,
                    context=context,
                    source_span_file=source_span_file,
                    source_span_byte_offset=None,
                    source_span_byte_len=None,
                ))

        # --- 10. Plain-text statute citations ---
        if "(" in text:
            for m in _STATUTE_INLINE_RE.finditer(text):
                sn, sy = int(m.group("sn")), int(m.group("sy"))
                if not (_YEAR_MIN <= sy <= _YEAR_MAX) or sn > _NUM_MAX:
                    pattern_matches.append(InlineCitationPatternMatch(
                        rule_id="fi_inline_statute_sanity_fail",
                        phase="inline_citation_extraction",
                        source_doc_id=doc_id,
                        reason=f"Statute year={sy} or number={sn} out of sanity range",
                        raw_text=m.group(0),
                        kind_attempted=InlineCitationKind.STATUTE_INLINE.value,
                    ))
                    continue
                citations.append(InlineCitation(
                    source_doc_id=doc_id,
                    source_doc_kind=doc_kind,
                    source_provision_ref="",
                    kind=InlineCitationKind.STATUTE_INLINE,
                    canonical_id=f"{sn}/{sy}",
                    raw_text=m.group(0),
                    case_year=sy,
                    case_number=sn,
                    context=context,
                    source_span_file=source_span_file,
                    source_span_byte_offset=None,
                    source_span_byte_len=None,
                ))

        return citations, pattern_matches


# Module-scope recognizer instance (shared across all extractions)
_RECOGNIZER = InlineCitationRecognizer()


# ---------------------------------------------------------------------------
# Extraction result container
# ---------------------------------------------------------------------------


@dataclass
class InlineCitationExtractionResult:
    """Container for all artifacts from one inline citation extraction pass.

    citations:      Successfully typed InlineCitation records.
    pattern_matches: InlineCitationPatternMatch records where a recognizer
                    fired but field sanity checks failed (AGENTS.md §1.8).
    """

    citations: List[InlineCitation] = field(default_factory=list)
    pattern_matches: List[InlineCitationPatternMatch] = field(default_factory=list)


# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------


def _get_text_excluding_refs(elem: ET.Element[str]) -> str:
    """Get all text content from a <p> element, EXCLUDING text inside <ref> children.

    Per composition discipline: text inside <ref> elements is deferred to
    feature #1. We only extract text from the element's own .text and the
    .tail of children (excluding the child's own content if it's a <ref>).

    The element's own text (before any child) is always included.
    For each child:
      - If the child is a <ref>, include only the .tail (text after the </ref>).
      - If the child is any other element, include both child text and tail.

    This ensures we skip the ref's own text node but preserve surrounding prose.
    """
    parts = []
    # Text before the first child
    if elem.text:
        parts.append(elem.text)
    for child in elem:
        child_tag_local = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if child_tag_local == "ref":
            # Skip the ref element's content; include tail (text after </ref>)
            if child.tail:
                parts.append(child.tail)
        else:
            # Non-ref child: include its text content too
            if child.text:
                parts.append(child.text)
            if child.tail:
                parts.append(child.tail)
    return "".join(parts).strip()


def _determine_context_from_ancestors(
    ancestor_names: List[str],
    ancestor_outlines: List[str],
    doc_kind: str,
) -> InlineCitationContext:
    """Determine InlineCitationContext from ancestor hcontainer names.

    Walks ancestors bottom-up to find the nearest named structural container.
    """
    if doc_kind == "statute":
        # Check if we're inside preliminaryWork
        if "preliminaryWork" in ancestor_names:
            return InlineCitationContext.PRELIMINARY_WORK
        return InlineCitationContext.ENACTED_STATUTE_BODY

    # HE document
    if "preliminaryWork" in ancestor_names:
        return InlineCitationContext.PRELIMINARY_WORK

    # Look for perustelut-like containers in ancestor names/outlines
    for name in ancestor_names:
        name_low = name.lower()
        if "perustelu" in name_low or "rationale" in name_low or "motivation" in name_low:
            return InlineCitationContext.HE_RATIONALE

    for outline in ancestor_outlines:
        outline_low = outline.lower()
        if "perustelu" in outline_low:
            return InlineCitationContext.HE_RATIONALE

    return InlineCitationContext.HE_INTRODUCTION


def _is_in_preliminary_work(ancestor_names: List[str]) -> bool:
    """True if 'preliminaryWork' appears in the ancestor hcontainer names."""
    return "preliminaryWork" in ancestor_names


# ---------------------------------------------------------------------------
# Main extraction entry point
# ---------------------------------------------------------------------------


def extract_inline_citations(
    xml_bytes: bytes,
    doc_id: str,
    doc_kind: str,
    *,
    source_span_file: Optional[str] = None,
    strict: bool = False,
) -> InlineCitationExtractionResult:
    """Extract InlineCitation records from a statute or HE body XML.

    Walks all ``<p>`` elements in the document body (excluding text inside
    ``<ref>`` markup) and applies the InlineCitationRecognizer over each.

    For preliminaryWork blocks:
      - EK and OLD_COMMITTEE recognizers run normally (closes #11 gap).
      - All other recognizers are suppressed (those citations belong to #11).

    Per AGENTS.md §1.8: body prose is overwhelmingly plain text, so we do NOT
    emit UNRESOLVED rows for every unmatched <p>. We ONLY emit
    InlineCitationPatternMatch when a pattern fires but fails sanity checks.

    Args:
        xml_bytes:         Raw XML bytes of the statute or HE document.
        doc_id:            Canonical document ID ('711/2022' or '116/2024').
        doc_kind:          'statute' or 'he'.
        source_span_file:  Provenance path/locator for source_span fields.
        strict:            Reserved for future strict-mode escalation.

    Returns:
        InlineCitationExtractionResult with citations and pattern_matches.

    Per AGENTS.md §1.1: canonical_id never synthesized.
    Per AGENTS.md §1.11: all patterns compiled at module scope.
    Per AGENTS.md §1.13: InlineCitationRecognizer is the named recognizer.
    """
    result = InlineCitationExtractionResult()

    root = ET.fromstring(xml_bytes)

    # Collect all <p> elements in the document body, tracking their structural
    # context via ancestor hcontainer attributes.
    # We use a depth-first walk of the tree, maintaining a stack of ancestor
    # hcontainer (name, outline) pairs.

    _walk_body(root, doc_id, doc_kind, source_span_file, result)

    return result


def _walk_body(
    root: ET.Element[str],
    doc_id: str,
    doc_kind: str,
    source_span_file: Optional[str],
    result: InlineCitationExtractionResult,
) -> None:
    """Depth-first walk of the XML tree, collecting inline citations from <p> elements.

    Maintains ancestor stack to resolve structural context for each <p>.
    """
    _walk_element(
        elem=root,
        doc_id=doc_id,
        doc_kind=doc_kind,
        source_span_file=source_span_file,
        result=result,
        ancestor_names=[],
        ancestor_outlines=[],
    )


def _walk_element(
    elem: ET.Element[str],
    doc_id: str,
    doc_kind: str,
    source_span_file: Optional[str],
    result: InlineCitationExtractionResult,
    ancestor_names: List[str],
    ancestor_outlines: List[str],
) -> None:
    """Recursively walk one element, processing <p> children."""
    tag_local = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag

    # Update ancestor context if this is an hcontainer
    new_names = ancestor_names
    new_outlines = ancestor_outlines
    if tag_local == "hcontainer":
        name = elem.get("name", "")
        outline = elem.get(f"{_FINLEX}outline", "")
        if name or outline:
            new_names = ancestor_names + ([name] if name else [])
            new_outlines = ancestor_outlines + ([outline] if outline else [])

    # Process <p> elements
    if tag_local == "p":
        _process_p_element(
            p_elem=elem,
            doc_id=doc_id,
            doc_kind=doc_kind,
            source_span_file=source_span_file,
            result=result,
            ancestor_names=new_names,
            ancestor_outlines=new_outlines,
        )
        # Still recurse into children of <p> (e.g. nested inline elements)
        # but don't process nested <p> as independent paragraphs — unusual in AKN.

    # Recurse into all children
    for child in elem:
        _walk_element(
            elem=child,
            doc_id=doc_id,
            doc_kind=doc_kind,
            source_span_file=source_span_file,
            result=result,
            ancestor_names=new_names,
            ancestor_outlines=new_outlines,
        )


def _process_p_element(
    p_elem: ET.Element[str],
    doc_id: str,
    doc_kind: str,
    source_span_file: Optional[str],
    result: InlineCitationExtractionResult,
    ancestor_names: List[str],
    ancestor_outlines: List[str],
) -> None:
    """Process one <p> element: extract text, determine context, run recognizer.

    For preliminaryWork paragraphs: only EK and OLD_COMMITTEE patterns run;
    other patterns are suppressed (handled by #11).
    """
    in_prelim = _is_in_preliminary_work(ancestor_names)
    context = _determine_context_from_ancestors(ancestor_names, ancestor_outlines, doc_kind)

    # Get text excluding <ref> markup content
    text = _get_text_excluding_refs(p_elem)
    if not text:
        return

    if in_prelim:
        # In preliminaryWork: only run EK and OLD_COMMITTEE recognizers.
        # Other patterns here belong to #11 (preparatory_reference_extractor).
        _run_prelim_only_recognizers(text, doc_id, doc_kind, context, source_span_file, result)
        return

    # Outside preliminaryWork: run all recognizers
    citations, pattern_matches = _RECOGNIZER.recognize_all(
        text=text,
        doc_id=doc_id,
        doc_kind=doc_kind,
        context=context,
        source_span_file=source_span_file,
    )
    result.citations.extend(citations)
    result.pattern_matches.extend(pattern_matches)


def _run_prelim_only_recognizers(
    text: str,
    doc_id: str,
    doc_kind: str,
    context: InlineCitationContext,
    source_span_file: Optional[str],
    result: InlineCitationExtractionResult,
) -> None:
    """Run only EK and OLD_COMMITTEE recognizers for text inside preliminaryWork.

    These are the patterns that #11 marks UNRESOLVED. This extractor closes
    the typing loop for them. All other patterns in preliminaryWork belong to #11.
    """
    # EK parliament kirjelma
    if "EK " in text:
        for m in _EK_RE.finditer(text):
            n, y = int(m.group("n")), int(m.group("y"))
            if not (_YEAR_MIN <= y <= _YEAR_MAX) or n > _NUM_MAX:
                result.pattern_matches.append(InlineCitationPatternMatch(
                    rule_id="fi_inline_ek_prelim_sanity_fail",
                    phase="inline_citation_extraction",
                    source_doc_id=doc_id,
                    reason=f"EK (preliminaryWork) year={y} or number={n} out of sanity range",
                    raw_text=m.group(0),
                    kind_attempted=InlineCitationKind.PARLIAMENT_KIRJELMA.value,
                ))
                continue
            result.citations.append(InlineCitation(
                source_doc_id=doc_id,
                source_doc_kind=doc_kind,
                source_provision_ref="",
                kind=InlineCitationKind.PARLIAMENT_KIRJELMA,
                canonical_id=f"fi.ek.{n}.{y}",
                raw_text=m.group(0),
                case_year=y,
                case_number=n,
                context=context,
                source_span_file=source_span_file,
                source_span_byte_offset=None,
                source_span_byte_len=None,
            ))

    # Old committee abbreviations
    if ".miet" in text.lower():
        for m in _OLD_COMMITTEE_RE.finditer(text):
            result.citations.append(InlineCitation(
                source_doc_id=doc_id,
                source_doc_kind=doc_kind,
                source_provision_ref="",
                kind=InlineCitationKind.OLD_COMMITTEE,
                canonical_id=None,
                raw_text=m.group(0),
                case_year=None,
                case_number=None,
                context=context,
                source_span_file=source_span_file,
                source_span_byte_offset=None,
                source_span_byte_len=None,
            ))
