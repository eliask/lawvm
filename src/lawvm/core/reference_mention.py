"""Core typed primitive for cross-statute citation references.

Promotes the Finland-frontend ``CrossRefEdge`` extraction to a stable typed
primitive that can be materialized as ``fi_refs.parquet`` and queried via
``lawvm refs`` and ``lawvm sql``.

Design principles (AGENTS.md §1.9, STRINGLY_TYPED_SURFACE_AUDIT.md):
  - Frozen dataclass with slots=True — no stringly-typed dicts crossing
    phase boundaries.
  - CiteKind and CiteConfidence are closed enums — not strings.
  - target_provision_ref is None only when cite_confidence is UNRESOLVED or
    BROKEN (typed absence, not missing key).
  - Rejected candidates emit RejectedRefCandidate — no silent drops
    (AGENTS.md §1.8).
  - Approximate/BROKEN confidence emits typed finding — no silent resolution
    (AGENTS.md §1.1).

This module has no Finland-specific imports. Finland extraction lives in
``lawvm.finland.cross_refs`` and ``lawvm.finland.ref_mention_extractor``.
This module only holds the shared typed primitive and observation types.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Optional, Tuple


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class CiteKind(Enum):
    """What kind of statute/instrument is cited."""

    INTERNAL = "internal"
    """Same-statute cross-reference (e.g. '3 luvun 5 §:ssä säädetään')."""

    CROSS_STATUTE = "cross_statute"
    """Reference to another Finnish enacted statute."""

    EU = "eu"
    """EU directive, regulation, or treaty."""

    TREATY = "treaty"
    """Bilateral or multilateral treaty (not EU-specific)."""

    NON_STATUTORY_INSTRUMENT = "non_statutory_instrument"
    """Finnish asetus, määräys, ohje issued under statute authority."""


class CiteConfidence(Enum):
    """How confidently the target provision reference was resolved."""

    EXACT = "exact"
    """Target resolves unambiguously from the source text."""

    APPROXIMATE = "approximate"
    """Heuristic resolution is defensible (e.g. agency lifecycle rename)."""

    AMBIGUOUS = "ambiguous"
    """Multiple plausible targets; cannot pick one."""

    UNRESOLVED = "unresolved"
    """Target cannot be resolved (typo, future statute, no match)."""

    BROKEN = "broken"
    """Target was repealed/renumbered after the citation was written."""


# ---------------------------------------------------------------------------
# Source span
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SourceSpan:
    """Provenance back to a byte range in a source file.

    Used by ReferenceMention to anchor citations to the exact location in the
    source XML where the citation phrase was found.

    Attributes:
        source_file: Path or URI of the source document.
        byte_offset:  0-based byte offset of the start of the span.
        byte_len:     Length of the span in bytes.
    """

    source_file: str
    byte_offset: int
    byte_len: int

    def __post_init__(self) -> None:
        if self.byte_offset < 0:
            raise ValueError("SourceSpan.byte_offset must be >= 0")
        if self.byte_len < 0:
            raise ValueError("SourceSpan.byte_len must be >= 0")


# ---------------------------------------------------------------------------
# Typed provision reference for citations
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProvisionRef:
    """Typed reference to a provision within a statute.

    Differs from core LegalAddress in that it carries the statute_id as a
    first-class field (cross-statute citations need both).

    Attributes:
        statute_id:      Canonical statute ID, e.g. "711/2022".
                         Empty string for internal cross-references where
                         the statute is implicit from the source context.
        provision_path:  Raw AKN provision path fragment (e.g. "sec_7_sub_3")
                         or empty string if only statute-level is cited.
        section_label:   Human-readable section label, e.g. "7", "7a".
                         Empty string if not parsed.
        subsection_num:  Subsection (momentti) number, or None.
        item_label:      Item (kohta) label, or None.
    """

    statute_id: str
    provision_path: str = ""
    section_label: str = ""
    subsection_num: Optional[int] = None
    item_label: Optional[str] = None

    def serialized(self) -> str:
        """Return a stable serialized form for parquet/JSONL output."""
        parts = [self.statute_id]
        if self.section_label:
            parts.append(self.section_label)
            if self.subsection_num is not None:
                parts.append(str(self.subsection_num))
                if self.item_label:
                    parts.append(self.item_label)
        return "/".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# ReferenceMention (the core typed primitive)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReferenceMention:
    """A typed mention of one statute or provision from within another provision.

    This is the stable typed primitive promoted from Finland's CrossRefEdge.
    It does NOT interpret the legal force of the reference (incorporation /
    delegation / constraint / authority-transfer) — that is interpretation,
    downstream of LawVM.

    The primitive types:
      - where the citation is (source_provision_ref)
      - where it points (target_provision_ref)
      - what kind of target it is (cite_kind)
      - how confidently the target was resolved (cite_confidence)
      - what syntactic class it belongs to (phrase_lemma)
      - where in the source text it lives (source_span)
      - when this reference state holds (valid_at_interval)
      - back-compat link to CrossRefEdge edge types (edge_subtype)

    Per AGENTS.md §1.1: target_provision_ref is None only when confidence
    is UNRESOLVED or BROKEN — never silently widened to a wrong target.
    Per AGENTS.md §1.8: rejected candidates produce RejectedRefCandidate.
    """

    source_provision_ref: ProvisionRef
    """Where the citation text lives."""

    target_provision_ref: Optional[ProvisionRef]
    """Where the citation points; None iff cite_confidence is UNRESOLVED."""

    cite_kind: CiteKind
    """What kind of instrument is cited."""

    cite_confidence: CiteConfidence
    """How confidently the target was resolved."""

    phrase_lemma: str
    """Syntactic class of the citation phrase.

    Values used by the Finland extractor:
      'ref_element'      — inline AKN <ref> element (most common)
      'REPEALS'          — metadata-level repeals edge
      'ISSUED_UNDER'     — metadata issuedUnderActs edge
      'ISSUES'           — metadata issuedUnderThisAct edge
      'in_prose_fi'      — in-prose Finnish citation pattern (future)
      'eu_text_pattern'  — EU citation from text scan
    """

    source_span: Optional[SourceSpan]
    """Provenance back to the source text; None for metadata-derived edges."""

    valid_at_interval: Tuple[Optional[date], Optional[date]]
    """(start, end) when this reference state holds; end=None = currently valid."""

    edge_subtype: Optional[str]
    """Back-compat with CrossRefEdge.edge_type: CITES / REPEALS / ISSUED_UNDER /
    ISSUES. None for in-prose citations extracted from body text."""

    target_stat_hash: Optional[str] = None
    """SHA256[:16] of the target statute's consolidated XML at projection time.
    Populated by the projection layer; None during extraction."""

    surface_text: str = ""
    """Literal source text for the citation surface when the extractor owns it.
    This is intentionally not part of the stable fi_refs row schema; neutral
    interlink projections use it for viewer overlays."""

    def __post_init__(self) -> None:
        if self.cite_confidence not in (CiteConfidence.UNRESOLVED, CiteConfidence.BROKEN):
            if self.target_provision_ref is None:
                raise ValueError(
                    "ReferenceMention.target_provision_ref may only be None "
                    "when cite_confidence is UNRESOLVED or BROKEN; "
                    f"got {self.cite_confidence!r}"
                )
        if not self.phrase_lemma:
            raise ValueError("ReferenceMention.phrase_lemma must be non-empty")


# ---------------------------------------------------------------------------
# Observation types (AGENTS.md §1.8 — no source lane disappears)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RejectedRefCandidate:
    """A citation candidate that was pattern-matched but rejected.

    Emitted when the extractor identifies text that LOOKS like a citation but
    fails grammar or sanity checks. Per AGENTS.md §1.8: no parse candidate
    disappears silently.

    Attributes:
        rule_id:          Stable rule identifier for the rejection reason.
        phase:            Pipeline phase ("cross_ref_extraction").
        source_statute_id: Statute the candidate was found in.
        reason:           Human-readable rejection reason.
        matched_text:     The text that triggered the candidate.
        source_span:      Location of the candidate in source, or None.
        blocking:         Whether this rejection blocks compilation.
        strict_disposition: What strict mode does with this record.
    """

    rule_id: str
    phase: str
    source_statute_id: str
    reason: str
    matched_text: str
    source_span: Optional[SourceSpan]
    blocking: bool = False
    strict_disposition: str = "record"


@dataclass(frozen=True, slots=True)
class BrokenReferenceFinding:
    """Finding emitted when a citation target was repealed after the citation.

    Per AGENTS.md §7: heuristics that affect legal text must have a stable
    rule ID, source witness, and finding emission. Broken references are not
    silently discarded — they remain as ReferenceMention with
    confidence=BROKEN and this finding in the audit lane.
    """

    rule_id: str
    phase: str
    source_statute_id: str
    target_statute_id: str
    source_provision_ref_str: str
    target_provision_ref_str: str
    reason: str
    blocking: bool = False
    strict_disposition: str = "record"


@dataclass(frozen=True, slots=True)
class AmbiguousReferenceFinding:
    """Finding emitted when a citation maps to multiple plausible targets.

    Per AGENTS.md §1.1: ambiguity must remain visible. The ReferenceMention
    is emitted with confidence=AMBIGUOUS; this finding names each candidate.
    """

    rule_id: str
    phase: str
    source_statute_id: str
    source_provision_ref_str: str
    candidate_target_ids: Tuple[str, ...]
    reason: str
    blocking: bool = False
    strict_disposition: str = "record"

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_target_ids", tuple(self.candidate_target_ids))


@dataclass(frozen=True, slots=True)
class ApproximateReferenceFinding:
    """Finding emitted when target resolved via lifecycle or renumbering heuristic.

    Per AGENTS.md §7: approximate recoveries must be witnessed.
    confidence=APPROXIMATE ReferenceMention always pairs with this finding.
    """

    rule_id: str
    phase: str
    source_statute_id: str
    source_provision_ref_str: str
    target_provision_ref_str: str
    heuristic_applied: str
    """Description of the lifecycle/renumbering heuristic used."""
    blocking: bool = False
    strict_disposition: str = "record"


# ---------------------------------------------------------------------------
# Parquet row serialization helpers
# ---------------------------------------------------------------------------


def reference_mention_to_row(mention: ReferenceMention) -> dict[str, object]:
    """Serialize a ReferenceMention to a flat dict for Parquet/JSONL output.

    Column names are stable per the brief's schema spec (REFERENCE_MENTION_EXTRACTION.md).
    Consumers must not depend on dict ordering; use column names.
    """
    src = mention.source_provision_ref
    tgt = mention.target_provision_ref

    valid_start, valid_end = mention.valid_at_interval
    span = mention.source_span

    return {
        "source_statute_id": src.statute_id,
        "source_provision_ref_str": src.serialized(),
        "target_statute_id": tgt.statute_id if tgt else None,
        "target_provision_ref_str": tgt.serialized() if tgt else None,
        "cite_kind": mention.cite_kind.value,
        "cite_confidence": mention.cite_confidence.value,
        "edge_subtype": mention.edge_subtype,
        "phrase_lemma": mention.phrase_lemma,
        "source_span_file": span.source_file if span else None,
        "source_span_byte_offset": span.byte_offset if span else None,
        "source_span_len": span.byte_len if span else None,
        "valid_at_start": valid_start.isoformat() if valid_start else None,
        "valid_at_end": valid_end.isoformat() if valid_end else None,
        "target_stat_hash": mention.target_stat_hash,
    }
