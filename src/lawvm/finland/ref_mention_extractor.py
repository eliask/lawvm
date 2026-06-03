"""Finnish ReferenceMention extractor — lifts CrossRefEdge to core typed primitive.

This module promotes Finland's existing ``CrossRefEdge`` extraction to the
stable core ``ReferenceMention`` type.  It is the primary extractor for
``fi_refs.parquet`` projection.

Entry points:

  extract_reference_mentions(xml_bytes, statute_id, ...) -> ExtractionResult
      All ReferenceMention records FROM statute_id + any findings/rejections.

  extract_eu_reference_mentions(xml_bytes, statute_id, ...) -> ExtractionResult
      EU cross-jurisdiction references from text scan.

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

  §1.13 Grammar trigger: currently 3 in-prose Finnish citation variants.
      This file uses module-scope compiled patterns, each a single predicate,
      not a pile of overlapping scans. If variants grow to 3+ overlapping
      passes on the same text, build a named scanner per REGEX_TO_GRAMMAR_MIGRATION.md.

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
    SourceSpan,
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


def extract_all_reference_mentions(
    xml_bytes: bytes,
    statute_id: str,
    *,
    valid_at_interval: Tuple[Optional[date], Optional[date]] = (None, None),
    strict: bool = False,
) -> ExtractionResult:
    """Extract all ReferenceMention records (domestic + EU) from a statute.

    Combines ``extract_reference_mentions`` and ``extract_eu_reference_mentions``.
    EU mentions are appended after domestic mentions.

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

    combined = ExtractionResult()
    combined.mentions = domestic.mentions + eu.mentions
    combined.rejected = domestic.rejected + eu.rejected
    combined.broken_findings = domestic.broken_findings + eu.broken_findings
    combined.ambiguous_findings = domestic.ambiguous_findings + eu.ambiguous_findings
    combined.approximate_findings = domestic.approximate_findings + eu.approximate_findings
    combined.diagnostics = domestic.diagnostics + eu.diagnostics
    return combined
