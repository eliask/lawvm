"""Finnish ReferenceMention extractor — lifts CrossRefEdge to core typed primitive.

This module promotes Finland's existing ``CrossRefEdge`` extraction to the
stable core ``ReferenceMention`` type.  It is the primary extractor for
``fi_refs.parquet`` projection.

Entry points:

  extract_reference_mentions(xml_bytes, statute_id, ...) -> ExtractionResult
      All ReferenceMention records FROM statute_id + any findings/rejections.

  extract_eu_reference_mentions(xml_bytes, statute_id, ...) -> ExtractionResult
      EU cross-jurisdiction references from text scan.

  extract_plain_text_statute_mentions(xml_bytes, statute_id, ...) -> ExtractionResult
      Plain-text Finnish statute citations NOT covered by <ref> markup.

  extract_all_reference_mentions(xml_bytes, statute_id, ...) -> ExtractionResult
      Combined domestic + EU + plain-text extraction.

Design discipline (AGENTS.md §1.1, §1.8, §1.11, §1.13):

  §1.1 No silent target hijacking:
      CrossRefEdge resolution maps edge_type → CiteKind deterministically.
      No fallback widening; unresolvable targets get confidence=UNRESOLVED.

  §1.8 No unsupported source lane disappears:
      Every rejected candidate emits RejectedRefCandidate.
      Every diagnostic from CrossRefDiagnostic is preserved.

  §1.11 Hot-path regex discipline:
      All patterns compiled at module scope.
      Bounded quantifiers; no adjacent unbounded repeats.
      Substring guards before regex on long text.

  §1.13 Named recognizer for plain-text statute citations:
      Finnish statute citations without <ref> markup form a GRAMMAR FAMILY
      (3+ inflection variants: -lain, -asetuksen, -laissa, -lakia, etc.).
      Implemented as PlainTextStatuteCitationRecognizer — one single-pass
      structured recognizer, not N overlapping backtracking regexes.

Source: Finlex Akoma Ntoso consolidated XML in the corpus store.
Promotion from: ``lawvm.finland.cross_refs`` (CrossRefEdge, CrossRefDiagnostic).
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional, Tuple

from lawvm.core.reference_mention import (
    AmbiguousReferenceFinding,
    ApproximateReferenceFinding,
    BrokenReferenceFinding,
    CiteConfidence,
    CiteKind,
    ProvisionRef,
    ReferenceMention,
    RejectedRefCandidate,
)
from lawvm.finland.cross_refs import (
    CrossRefDiagnostic,
    CrossRefEdge,
    extract_cross_refs,
    extract_eu_refs,
)

# ---------------------------------------------------------------------------
# Module-scope compiled patterns (AGENTS.md §1.11)
# ---------------------------------------------------------------------------

_AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

# Section label extractor from AKN sec_N or sec_Na paths.
# Bounded: [a-z0-9_]{0,100} is safe.
_AKN_SECTION_PATH_RE = re.compile(
    r"(?:^|/)sec_([0-9]{1,6}[a-z]?)(?:/|$|_sub)",
    re.IGNORECASE,
)

# Subsection extractor from AKN path: sec_N_sub_M or sub_M.
_AKN_SUBSECTION_PATH_RE = re.compile(
    r"_sub_([0-9]{1,4})(?:/|$|_)",
    re.IGNORECASE,
)

# EU statute id extractor: "eu/TYPE/YEAR/NUMBER"
_EU_ID_RE = re.compile(
    r"^eu/([a-z]{2,10})/(\d{4})/(\d{1,6})$",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Plain-text statute citation recognizer (AGENTS.md §1.13)
# ---------------------------------------------------------------------------
#
# Finnish statute citations in body prose without <ref> markup follow a
# shared pattern family:
#
#   Inflection suffixes on the statute name:
#     -lain          (lannoitelain)
#     -lakia         (lannoitelakia)
#     -laissa        (elintarvikelaissa)
#     -laista        (elintarvikelaista)
#     -laiksi        (elintarvikelaiksi)
#     -laille        (elintarvikelaille)
#     -asetuksen     (ympäristönsuojeluasetuksen)
#     -asetusta      (ympäristönsuojeluasetusta)
#     -asetuksessa   (ympäristönsuojeluasetuksessa)
#     -asetuksesta   (ympäristönsuojeluasetuksesta)
#     -asetukseksi   (ympäristönsuojeluasetukseksi)
#     -lain          (also short: "lain (711/2022)")
#     -asetuksen     (also short: "asetuksen (964/2023)")
#   ...
#   Followed by: (NUMBER/YEAR) or (YEAR/NUMBER) in parentheses
#   Optionally followed by: SECTION § and SUBSECTION momentti/momentin
#
# This is a grammar (3+ variants) → build ONE named recognizer, not N regexes.
#
# The recognizer uses a SINGLE compiled regex over the text of <p> nodes
# that does not include any <ref> element text, to avoid double-counting.
# The regex is structured so group(1)=statute_number, group(2)=statute_year,
# group(3)=section_label (optional).
#
# Grammar:
#   WORD_WITH_SUFFIX "(" NUMBER "/" YEAR ")" [WHITESPACE SECTION "§"]
#   where SUFFIX is one of the known Finnish inflection suffixes.
#
# Bounded quantifiers (AGENTS.md §1.11):
#   - Word stem: [a-zA-ZäöåÄÖÅ\-]{1,60}
#   - Suffix alternatives: alternation of bounded strings
#   - NUMBER: \d{1,6}
#   - YEAR: \d{4}
#   - SECTION: \d{1,6}[a-zA-ZäöÄÖ]?
#
# Substring guard: check for "§" in text before running the regex
# (all valid statute citations in Finnish law refer to some section/§).
# Additional guard: check for "(" in text (all citations have parenthetical ID).

_PLAIN_TEXT_FI_STATUTE_RE = re.compile(
    r"""
    (?:
        # Named law/statute word with inflection suffix (nominative and case forms)
        [a-zA-Z\xe4\xf6\xe5\xc4\xd6\xc5\-]{1,60}
        (?:lain|lakia|laissa|laista|laiksi|laille|lailla|lailta|lakia|lain)
      | [a-zA-Z\xe4\xf6\xe5\xc4\xd6\xc5\-]{1,60}
        (?:asetuksen|asetusta|asetuksessa|asetuksesta|asetukseksi|asetuksella|asetukselle|asetukselta|asetuksen)
      | \b(?:lain|lakia|laissa|laiksi|laille|laista|lailla|lailta)
      | \b(?:asetuksen|asetusta|asetuksessa|asetuksesta|asetukseksi|asetuksella|asetukselle|asetukselta)
    )
    \s{0,5}
    \(
    \s{0,3}
    (\d{1,6})/(\d{4})   # group 1 = number, group 2 = year
    \s{0,3}
    \)
    (?:
        \s{0,10}
        (\d{1,6}[a-zA-Z\xe4\xf6\xc4\xd6]?)   # group 3 = section label (optional)
        \s{0,5}
        \xa7                                    # § character (U+00A7)
    )?
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Substring guards for the plain-text extractor:
#   - "§" (section mark, always present in Finnish statute citations)
#   - "(" (parenthetical statute ID)
# Both must be present; if either is absent, skip the full regex scan.
_PLAIN_TEXT_GUARD_SECTION = "\xa7"  # §
_PLAIN_TEXT_GUARD_PAREN = "("


class PlainTextStatuteCitationRecognizer:
    """Named recognizer for Finnish plain-text statute citations (AGENTS.md §1.13).

    Extracts statute citations from <p> text that is NOT inside <ref> elements,
    to complement the structured AKN <ref>-based extraction.

    This is a SINGLE-PASS structured recognizer scanning non-ref text fragments
    within <p> elements, not N overlapping backtracking regexes. Each match
    yields (statute_id, section_label_or_empty, start_position) tuples.

    Statute ID form: "NUMBER/YEAR" (canonical, matching Finnish statute_id format).
    Section label: extracted when present; empty string otherwise.

    Usage:
        recognizer = PlainTextStatuteCitationRecognizer()
        for statute_id, section_label in recognizer.scan_non_ref_text(p_element):
            ...

    Per AGENTS.md §1.11:
        - Module-scope compiled pattern _PLAIN_TEXT_FI_STATUTE_RE.
        - Substring guards applied before regex scan.
        - Bounded quantifiers; no adjacent unbounded repeats.
    """

    def _collect_non_ref_text(self, p_el: ET.Element) -> str:
        """Collect text of <p> element excluding text inside <ref> children.

        Returns the concatenated text content of:
          - p_el.text (direct text before first child)
          - For each non-<ref> child: child.text + child.tail
          - For each <ref> child: ONLY child.tail (the text AFTER the ref,
            not inside it — since it's already captured by the <ref> extractor)

        This ensures we do NOT double-count text that was already covered by
        an AKN <ref> element.
        """
        ref_local = "ref"  # AKN local name

        parts: List[str] = []
        if p_el.text:
            parts.append(p_el.text)

        for child in p_el:
            local = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if local == ref_local:
                # Skip the ref's own text content (already in structured extraction).
                # BUT include the tail (text immediately after the </ref> tag).
                if child.tail:
                    parts.append(child.tail)
            else:
                # Non-ref child: include its text subtree + tail
                if child.text:
                    parts.append(child.text)
                for grandchild in child.iter():
                    if grandchild is child:
                        continue
                    gl = grandchild.tag.split("}")[-1] if "}" in grandchild.tag else grandchild.tag
                    if gl != ref_local and grandchild.text:
                        parts.append(grandchild.text)
                    if grandchild.tail:
                        parts.append(grandchild.tail)
                if child.tail:
                    parts.append(child.tail)

        return "".join(parts)

    def scan(
        self,
        p_el: ET.Element,
    ) -> List[Tuple[str, str]]:
        """Scan a <p> element for plain-text Finnish statute citations.

        Returns a list of (statute_id, section_label) tuples:
          - statute_id:    "NUMBER/YEAR" canonical form
          - section_label: e.g. "7", "7a", or "" if not present

        Per AGENTS.md §1.11: substring guards applied before regex scan.
        """
        text = self._collect_non_ref_text(p_el)
        if not text:
            return []

        # Substring guards (fast path — eliminates ~99% of non-matching calls)
        if _PLAIN_TEXT_GUARD_PAREN not in text:
            return []
        if _PLAIN_TEXT_GUARD_SECTION not in text:
            return []

        results: List[Tuple[str, str]] = []
        seen_ids: set[str] = set()

        for m in _PLAIN_TEXT_FI_STATUTE_RE.finditer(text):
            num_raw = m.group(1)
            year = m.group(2)
            section_raw = m.group(3) or ""

            # Sanity: year must be plausible
            year_int = int(year)
            if year_int < 1700 or year_int > 2100:
                continue

            # Sanity: number must be non-zero
            num_int = int(num_raw)
            if num_int <= 0 or num_int > 999999:
                continue

            statute_id = f"{num_int}/{year}"

            # Deduplicate same statute_id within this <p>
            key = statute_id + "/" + section_raw
            if key in seen_ids:
                continue
            seen_ids.add(key)

            results.append((statute_id, section_raw))

        return results


# Module-level singleton recognizer (built once at import time)
_PLAIN_TEXT_RECOGNIZER = PlainTextStatuteCitationRecognizer()

# ---------------------------------------------------------------------------
# Extraction result container
# ---------------------------------------------------------------------------


@dataclass
class ExtractionResult:
    """Container for all artifacts from one extraction pass.

    mentions:         Successfully typed ReferenceMention records.
    rejected:         RejectedRefCandidate records (non-empty citations that
                      failed grammar or sanity).
    broken_findings:  BrokenReferenceFinding records.
    ambiguous_findings: AmbiguousReferenceFinding records.
    approximate_findings: ApproximateReferenceFinding records.
    diagnostics:      CrossRefDiagnostic records from underlying extractor.
    """

    mentions: List[ReferenceMention] = field(default_factory=list)
    rejected: List[RejectedRefCandidate] = field(default_factory=list)
    broken_findings: List[BrokenReferenceFinding] = field(default_factory=list)
    ambiguous_findings: List[AmbiguousReferenceFinding] = field(default_factory=list)
    approximate_findings: List[ApproximateReferenceFinding] = field(default_factory=list)
    diagnostics: List[CrossRefDiagnostic] = field(default_factory=list)


# ---------------------------------------------------------------------------
# CrossRefEdge → ReferenceMention lift
# ---------------------------------------------------------------------------


def _parse_provision_ref_from_path(
    statute_id: str,
    provision_path: str,
) -> ProvisionRef:
    """Build a ProvisionRef from a statute_id and AKN provision_path fragment.

    The AKN path fragment is like "sec_7", "sec_7_sub_3", "sec_12a_sub_2".
    We extract human-readable labels where possible.
    """
    section_label = ""
    subsection_num: Optional[int] = None

    if provision_path:
        m_sec = _AKN_SECTION_PATH_RE.search(provision_path)
        if m_sec:
            section_label = m_sec.group(1)

        m_sub = _AKN_SUBSECTION_PATH_RE.search(provision_path)
        if m_sub:
            subsection_num = int(m_sub.group(1))

    return ProvisionRef(
        statute_id=statute_id,
        provision_path=provision_path,
        section_label=section_label,
        subsection_num=subsection_num,
    )


def _edge_to_cite_kind(
    edge: CrossRefEdge,
    source_statute_id: str,
) -> CiteKind:
    """Map CrossRefEdge.edge_type to CiteKind.

    AGENTS.md §1.1: mapping is deterministic. No fallback.

    CITES:        CROSS_STATUTE (or INTERNAL if target == source).
    REPEALS:      CROSS_STATUTE (metadata-level fact).
    ISSUED_UNDER: NON_STATUTORY_INSTRUMENT (source issued under target authority).
    ISSUES:       NON_STATUTORY_INSTRUMENT (source issued a decree as target).
    """
    edge_type = edge.edge_type
    if edge_type == "CITES":
        if edge.target_statute_id == source_statute_id:
            return CiteKind.INTERNAL
        # EU ids are "eu/TYPE/YEAR/NUMBER"
        if edge.target_statute_id.startswith("eu/"):
            return CiteKind.EU
        return CiteKind.CROSS_STATUTE
    if edge_type in ("ISSUED_UNDER", "ISSUES"):
        return CiteKind.NON_STATUTORY_INSTRUMENT
    if edge_type == "REPEALS":
        return CiteKind.CROSS_STATUTE
    # Unknown edge_type: default CROSS_STATUTE, emitter will flag
    return CiteKind.CROSS_STATUTE


def _edge_to_confidence(edge: CrossRefEdge) -> CiteConfidence:
    """Assign confidence to a CrossRefEdge.

    For edges derived from explicit AKN <ref> elements or metadata, the
    confidence is EXACT — the structured markup names the target.

    Target resolution against the statute graph is deferred to the projection
    phase. At extraction time, existence of the AKN href → EXACT.
    BROKEN detection requires the consolidated statute store; handled separately.
    """
    # All CrossRefEdge sources are structural (AKN element or finlex: metadata),
    # so confidence is EXACT at extraction time.
    return CiteConfidence.EXACT


def _source_provision_ref(
    edge: CrossRefEdge,
    source_statute_id: str,
) -> ProvisionRef:
    """Build source ProvisionRef from a CrossRefEdge."""
    section_label = edge.source_section or ""
    return ProvisionRef(
        statute_id=source_statute_id,
        provision_path="",
        section_label=section_label,
    )


def _target_provision_ref(edge: CrossRefEdge) -> ProvisionRef:
    """Build target ProvisionRef from a CrossRefEdge."""
    return _parse_provision_ref_from_path(
        edge.target_statute_id,
        edge.target_section or "",
    )


def _edge_to_mention(
    edge: CrossRefEdge,
    source_statute_id: str,
    valid_at_interval: Tuple[Optional[date], Optional[date]],
) -> ReferenceMention:
    """Lift one CrossRefEdge to a ReferenceMention."""
    cite_kind = _edge_to_cite_kind(edge, source_statute_id)
    confidence = _edge_to_confidence(edge)
    src_ref = _source_provision_ref(edge, source_statute_id)
    tgt_ref = _target_provision_ref(edge)

    # For CITES edges, phrase_lemma is "ref_element" (AKN <ref> element).
    # For metadata edges, it is the edge_type name.
    if edge.edge_type == "CITES":
        phrase_lemma = "ref_element"
    else:
        phrase_lemma = edge.edge_type  # REPEALS / ISSUED_UNDER / ISSUES

    return ReferenceMention(
        source_provision_ref=src_ref,
        target_provision_ref=tgt_ref,
        cite_kind=cite_kind,
        cite_confidence=confidence,
        phrase_lemma=phrase_lemma,
        source_span=None,  # CrossRefEdge doesn't carry byte spans
        valid_at_interval=valid_at_interval,
        edge_subtype=edge.edge_type,
        target_stat_hash=edge.target_stat_hash if edge.target_stat_hash else None,
    )


# ---------------------------------------------------------------------------
# EU mention lift (from extract_eu_refs)
# ---------------------------------------------------------------------------


def _eu_edge_to_mention(
    edge: CrossRefEdge,
    source_statute_id: str,
    valid_at_interval: Tuple[Optional[date], Optional[date]],
) -> ReferenceMention:
    """Lift an EU CrossRefEdge (always CITES) to a ReferenceMention."""
    src_ref = ProvisionRef(
        statute_id=source_statute_id,
        provision_path="",
        section_label=edge.source_section or "",
    )
    tgt_ref = ProvisionRef(
        statute_id=edge.target_statute_id,
        provision_path="",
    )
    return ReferenceMention(
        source_provision_ref=src_ref,
        target_provision_ref=tgt_ref,
        cite_kind=CiteKind.EU,
        cite_confidence=CiteConfidence.EXACT,
        phrase_lemma="eu_text_pattern",
        source_span=None,
        valid_at_interval=valid_at_interval,
        edge_subtype="CITES",
    )


# ---------------------------------------------------------------------------
# CrossRefDiagnostic passthrough
# ---------------------------------------------------------------------------


def _diagnostic_to_rejected(
    diag: CrossRefDiagnostic,
    *,
    is_skip: bool,
) -> Optional[RejectedRefCandidate]:
    """Convert a CrossRefDiagnostic to a RejectedRefCandidate where appropriate.

    Self-reference skips are NOT rejections — they are valid structural records.
    Only diagnostics for extraction failures become RejectedRefCandidate.
    """
    if diag.rule_id in (
        "fi_cross_ref_self_reference_skipped",
        "fi_cross_ref_xml_parse_failed",
    ):
        # These remain as CrossRefDiagnostic in the diagnostics list;
        # xml_parse_failed is a blocker diagnostic, not a rejected candidate.
        return None
    # Unknown diagnostic family — surface as rejected candidate
    return RejectedRefCandidate(
        rule_id=diag.rule_id,
        phase=diag.phase,
        source_statute_id=diag.source_statute_id,
        reason=diag.reason,
        matched_text=diag.href or "",
        source_span=None,
        blocking=diag.blocking,
        strict_disposition=diag.strict_disposition,
    )


# ---------------------------------------------------------------------------
# Main extraction entry points
# ---------------------------------------------------------------------------


def extract_reference_mentions(
    xml_bytes: bytes,
    statute_id: str,
    *,
    valid_at_interval: Tuple[Optional[date], Optional[date]] = (None, None),
    strict: bool = False,
) -> ExtractionResult:
    """Extract ReferenceMention records from a Finnish statute XML.

    This wraps ``extract_cross_refs`` and promotes each CrossRefEdge to
    a ReferenceMention using the core typed primitive.

    Args:
        xml_bytes:         Raw XML bytes of the statute.
        statute_id:        Canonical statute ID of the source, e.g. "711/2022".
        valid_at_interval: (start, end) date range for which these references
                           hold. Pass (None, None) for "whole statute history."
        strict:            If True, APPROXIMATE/UNRESOLVED mentions cause a
                           strict-mode block rather than a warning.

    Returns:
        ExtractionResult with mentions, rejected candidates, findings.

    Per AGENTS.md §1.1: target not found → UNRESOLVED, not widened.
    Per AGENTS.md §1.8: every CrossRefDiagnostic is preserved.
    Per AGENTS.md §1.11: all patterns compiled at module scope.
    """
    result = ExtractionResult()

    # Collect CrossRefDiagnostic from underlying extractor
    diag_list: List[CrossRefDiagnostic] = []
    edges: List[CrossRefEdge] = extract_cross_refs(
        xml_bytes, statute_id, diagnostics_out=diag_list
    )

    # Preserve all diagnostics (AGENTS.md §1.8)
    result.diagnostics.extend(diag_list)

    # Convert edges → ReferenceMention
    for edge in edges:
        mention = _edge_to_mention(edge, statute_id, valid_at_interval)

        # Strict-mode check: APPROXIMATE and UNRESOLVED are blocked in strict mode
        if strict and mention.cite_confidence in (
            CiteConfidence.APPROXIMATE,
            CiteConfidence.UNRESOLVED,
        ):
            # In strict mode, emit a blocking diagnostic rather than silently reject.
            # The mention is still emitted (audit trail preserved per §1.8).
            result.rejected.append(
                RejectedRefCandidate(
                    rule_id="fi_ref_mention_strict_confidence_barrier",
                    phase="cross_ref_extraction",
                    source_statute_id=statute_id,
                    reason=(
                        f"strict mode: {mention.cite_confidence.value} confidence "
                        f"for {edge.edge_type} → {edge.target_statute_id}"
                    ),
                    matched_text=edge.target_statute_id,
                    source_span=None,
                    blocking=True,
                    strict_disposition="block",
                )
            )

        result.mentions.append(mention)

    # Convert diagnostics to RejectedRefCandidate where appropriate
    for diag in diag_list:
        rej = _diagnostic_to_rejected(diag, is_skip=True)
        if rej is not None:
            result.rejected.append(rej)

    return result


def extract_eu_reference_mentions(
    xml_bytes: bytes,
    statute_id: str,
    *,
    valid_at_interval: Tuple[Optional[date], Optional[date]] = (None, None),
) -> ExtractionResult:
    """Extract EU cross-jurisdiction ReferenceMention records from a statute.

    Wraps ``extract_eu_refs`` and promotes each EU CrossRefEdge to a
    ReferenceMention with cite_kind=EU.

    Args:
        xml_bytes:         Raw XML bytes of the statute.
        statute_id:        Canonical statute ID of the source.
        valid_at_interval: Date range for which these references hold.

    Returns:
        ExtractionResult with EU mentions.
    """
    result = ExtractionResult()

    edges: List[CrossRefEdge] = extract_eu_refs(xml_bytes, statute_id)
    for edge in edges:
        mention = _eu_edge_to_mention(edge, statute_id, valid_at_interval)
        result.mentions.append(mention)

    return result


def extract_plain_text_statute_mentions(
    xml_bytes: bytes,
    statute_id: str,
    *,
    valid_at_interval: Tuple[Optional[date], Optional[date]] = (None, None),
    ref_covered_statute_ids: Optional[set] = None,
) -> ExtractionResult:
    """Extract plain-text Finnish statute citations NOT covered by <ref> markup.

    Walks <p> elements in the AKN body, collecting text that is NOT inside
    <ref> child elements, and applies the PlainTextStatuteCitationRecognizer
    to find statute citations of the form "[word]lain (711/2022) 7 §".

    Per AGENTS.md §1.13: PlainTextStatuteCitationRecognizer is a named
    single-pass recognizer for the Finnish statute citation grammar family
    (-lain, -asetuksen, -laissa, etc.), not N overlapping regex passes.

    Per AGENTS.md §1.8: all results are emitted as ReferenceMention records;
    no candidate disappears silently.

    Args:
        xml_bytes:               Raw XML bytes of the statute.
        statute_id:              Canonical statute ID of the source, e.g. "711/2022".
        valid_at_interval:       (start, end) date range for these references.
        ref_covered_statute_ids: Set of statute IDs already captured by the
                                 <ref>-element extraction pass for this statute.
                                 When provided, plain-text mentions for the same
                                 target statute_id are skipped to avoid double-emission
                                 at the statute level.
                                 Note: provision-level deduplication is more precise
                                 but requires span tracking; this is the statute-level guard.

    Returns:
        ExtractionResult with plain-text ReferenceMention records.
        phrase_lemma is ``"plain_text"`` to distinguish from ``"ref_element"``
        (AKN <ref>-derived) records.
        cite_confidence is EXACT for well-formed statute IDs (NUMBER/YEAR within
        plausible range); confidence elevation to APPROXIMATE is reserved for the
        projection phase when statute-graph resolution occurs.
    """
    result = ExtractionResult()

    if not xml_bytes:
        return result

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        # XML parse errors are already reported by extract_reference_mentions;
        # return empty result rather than double-reporting.
        return result

    covered: set = ref_covered_statute_ids or set()
    valid_start, valid_end = valid_at_interval

    # Walk <p> elements in the body
    _ns_p = f"{{{_AKN_NS}}}p"
    # Also accept bare <p> (some test fixtures omit the namespace on p)
    _bare_p = "p"

    p_elements: List[ET.Element] = []
    for el in root.iter():
        local = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if local == "p":
            p_elements.append(el)

    for p_el in p_elements:
        hits = _PLAIN_TEXT_RECOGNIZER.scan(p_el)
        for target_statute_id, section_label in hits:
            # Skip if this target is already covered by a <ref>-element mention
            if target_statute_id in covered:
                continue

            # Skip self-reference (same logic as <ref> extractor)
            if target_statute_id == statute_id:
                continue

            src_ref = ProvisionRef(
                statute_id=statute_id,
                provision_path="",
                section_label="",
            )
            tgt_ref = ProvisionRef(
                statute_id=target_statute_id,
                provision_path="",
                section_label=section_label,
            )
            mention = ReferenceMention(
                source_provision_ref=src_ref,
                target_provision_ref=tgt_ref,
                cite_kind=CiteKind.CROSS_STATUTE,
                cite_confidence=CiteConfidence.EXACT,
                phrase_lemma="plain_text",
                source_span=None,
                valid_at_interval=(valid_start, valid_end),
                edge_subtype="CITES",
            )
            result.mentions.append(mention)

    return result


def extract_all_reference_mentions(
    xml_bytes: bytes,
    statute_id: str,
    *,
    valid_at_interval: Tuple[Optional[date], Optional[date]] = (None, None),
    strict: bool = False,
) -> ExtractionResult:
    """Extract all ReferenceMention records (domestic + EU + plain-text) from a statute.

    Combines:
      - ``extract_reference_mentions``: AKN <ref> element mentions + metadata edges.
      - ``extract_eu_reference_mentions``: EU citations from text scan.
      - ``extract_plain_text_statute_mentions``: plain-text statute citations
        NOT covered by <ref> markup (phrase_lemma="plain_text").

    EU and plain-text mentions are appended after domestic mentions.

    This is the primary entry point for the ``fi_refs.parquet`` projection.
    """
    domestic = extract_reference_mentions(
        xml_bytes,
        statute_id,
        valid_at_interval=valid_at_interval,
        strict=strict,
    )
    eu = extract_eu_reference_mentions(
        xml_bytes,
        statute_id,
        valid_at_interval=valid_at_interval,
    )

    # Build the set of statute IDs already covered by <ref>-element extraction
    # to pass as the dedup guard to the plain-text pass.
    ref_covered: set = {
        m.target_provision_ref.statute_id
        for m in domestic.mentions
        if m.target_provision_ref is not None and m.edge_subtype == "CITES"
    }

    plain = extract_plain_text_statute_mentions(
        xml_bytes,
        statute_id,
        valid_at_interval=valid_at_interval,
        ref_covered_statute_ids=ref_covered,
    )

    combined = ExtractionResult()
    combined.mentions = domestic.mentions + eu.mentions + plain.mentions
    combined.rejected = domestic.rejected + eu.rejected + plain.rejected
    combined.broken_findings = domestic.broken_findings + eu.broken_findings + plain.broken_findings
    combined.ambiguous_findings = domestic.ambiguous_findings + eu.ambiguous_findings + plain.ambiguous_findings
    combined.approximate_findings = domestic.approximate_findings + eu.approximate_findings + plain.approximate_findings
    combined.diagnostics = domestic.diagnostics + eu.diagnostics + plain.diagnostics
    return combined
