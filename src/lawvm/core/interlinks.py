"""Jurisdiction-neutral legal citation and interlink primitives.

This module is the common substrate for citation-like facts.  Jurisdiction
frontends own recognition and resolution.  Consumers, including viewers, receive
typed mention/link rows and must not parse legal prose themselves.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Optional, Tuple

from lawvm.core.locator import HierarchicalLocator


class InterlinkSurfaceKind(Enum):
    XML_REF = "xml_ref"
    PROSE_REF = "prose_ref"
    METADATA_REF = "metadata_ref"
    PREPARATORY_REF = "preparatory_ref"
    EFFECT_FEED_REF = "effect_feed_ref"
    MANUAL_CLAIM_REF = "manual_claim_ref"


class InterlinkRole(Enum):
    CITES = "cites"
    AMENDS = "amends"
    REPEALS = "repeals"
    ISSUED_UNDER = "issued_under"
    PREPARATORY_HISTORY = "preparatory_history"
    AUTHORITY = "authority"
    APPLICATION_SCOPE = "application_scope"
    UNKNOWN = "unknown"


class InterlinkResolutionStatus(Enum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    AMBIGUOUS = "ambiguous"
    BROKEN = "broken"
    EXTERNAL_ONLY = "external_only"


class InterlinkConfidence(Enum):
    EXACT = "exact"
    HEURISTIC = "heuristic"
    FALLBACK = "fallback"
    LEGACY_UNKNOWN = "legacy_unknown"


@dataclass(frozen=True, slots=True)
class LegalWorkRef:
    """Jurisdiction-neutral reference to a legal or legal-adjacent work."""

    jurisdiction: str
    work_kind: str
    local_id: str
    canonical_id: str = ""

    def __post_init__(self) -> None:
        if not self.jurisdiction:
            raise ValueError("LegalWorkRef.jurisdiction must be non-empty")
        if not self.work_kind:
            raise ValueError("LegalWorkRef.work_kind must be non-empty")
        if not self.local_id:
            raise ValueError("LegalWorkRef.local_id must be non-empty")


@dataclass(frozen=True, slots=True)
class LegalLocatorRef:
    """A work-local structural locator plus its unresolved source form."""

    locator: Optional[HierarchicalLocator] = None
    raw_locator: str = ""
    resolver_namespace: str = ""

    def serialized(self) -> str:
        if self.locator is not None:
            return str(self.locator)
        return self.raw_locator


@dataclass(frozen=True, slots=True)
class InterlinkSourceSpan:
    source_artifact_id: str
    byte_offset: Optional[int] = None
    byte_len: Optional[int] = None

    def __post_init__(self) -> None:
        if not self.source_artifact_id:
            raise ValueError("InterlinkSourceSpan.source_artifact_id must be non-empty")
        if self.byte_offset is not None and self.byte_offset < 0:
            raise ValueError("InterlinkSourceSpan.byte_offset must be >= 0")
        if self.byte_len is not None and self.byte_len < 0:
            raise ValueError("InterlinkSourceSpan.byte_len must be >= 0")


@dataclass(frozen=True, slots=True)
class RenderedTextSpan:
    """PIT/viewer placement of an already-resolved surface mention."""

    statute_id: str
    effective_date: str
    address: str
    segment_index: int
    char_start: int
    char_end: int
    surface_text: str

    def __post_init__(self) -> None:
        if not self.statute_id:
            raise ValueError("RenderedTextSpan.statute_id must be non-empty")
        if not self.effective_date:
            raise ValueError("RenderedTextSpan.effective_date must be non-empty")
        if not self.address:
            raise ValueError("RenderedTextSpan.address must be non-empty")
        if self.segment_index < 0:
            raise ValueError("RenderedTextSpan.segment_index must be >= 0")
        if self.char_start < 0 or self.char_end < self.char_start:
            raise ValueError("RenderedTextSpan char bounds are invalid")
        if not self.surface_text:
            raise ValueError("RenderedTextSpan.surface_text must be non-empty")


@dataclass(frozen=True, slots=True)
class InterlinkTarget:
    work: Optional[LegalWorkRef]
    locator: Optional[LegalLocatorRef] = None
    url: str = ""
    candidate_work_ids: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_work_ids", tuple(self.candidate_work_ids))


@dataclass(frozen=True, slots=True)
class LegalInterlink:
    interlink_id: str
    source_work: LegalWorkRef
    source_locator: Optional[LegalLocatorRef]
    source_span: Optional[InterlinkSourceSpan]
    rendered_span: Optional[RenderedTextSpan]
    surface_text: str
    surface_kind: InterlinkSurfaceKind
    target: InterlinkTarget
    role: InterlinkRole
    resolution_status: InterlinkResolutionStatus
    confidence: InterlinkConfidence
    resolver_id: str
    valid_at_interval: Tuple[Optional[date], Optional[date]] = (None, None)
    detail_json: str = "{}"

    def __post_init__(self) -> None:
        if not self.interlink_id:
            raise ValueError("LegalInterlink.interlink_id must be non-empty")
        if not self.surface_text:
            raise ValueError("LegalInterlink.surface_text must be non-empty")
        if not self.resolver_id:
            raise ValueError("LegalInterlink.resolver_id must be non-empty")
        if self.resolution_status == InterlinkResolutionStatus.RESOLVED and self.target.work is None:
            raise ValueError("resolved LegalInterlink requires target.work")


def _work_id(work: Optional[LegalWorkRef]) -> Optional[str]:
    if work is None:
        return None
    return work.canonical_id or f"{work.jurisdiction}:{work.work_kind}:{work.local_id}"


def legal_interlink_to_row(link: LegalInterlink) -> dict[str, object]:
    """Serialize a LegalInterlink to a stable flat projection row."""
    target_locator = link.target.locator.serialized() if link.target.locator else None
    source_locator = link.source_locator.serialized() if link.source_locator else None
    valid_start, valid_end = link.valid_at_interval
    return {
        "interlink_id": link.interlink_id,
        "source_jurisdiction": link.source_work.jurisdiction,
        "source_work_kind": link.source_work.work_kind,
        "source_local_id": link.source_work.local_id,
        "source_work_id": _work_id(link.source_work),
        "source_locator": source_locator,
        "surface_text": link.surface_text,
        "surface_kind": link.surface_kind.value,
        "role": link.role.value,
        "target_jurisdiction": link.target.work.jurisdiction if link.target.work else None,
        "target_work_kind": link.target.work.work_kind if link.target.work else None,
        "target_local_id": link.target.work.local_id if link.target.work else None,
        "target_work_id": _work_id(link.target.work),
        "target_locator": target_locator,
        "target_url": link.target.url or None,
        "candidate_work_ids": "|".join(link.target.candidate_work_ids) if link.target.candidate_work_ids else None,
        "resolution_status": link.resolution_status.value,
        "confidence": link.confidence.value,
        "resolver_id": link.resolver_id,
        "source_artifact_id": link.source_span.source_artifact_id if link.source_span else None,
        "source_span_byte_offset": link.source_span.byte_offset if link.source_span else None,
        "source_span_byte_len": link.source_span.byte_len if link.source_span else None,
        "rendered_statute_id": link.rendered_span.statute_id if link.rendered_span else None,
        "rendered_effective_date": link.rendered_span.effective_date if link.rendered_span else None,
        "rendered_address": link.rendered_span.address if link.rendered_span else None,
        "rendered_segment_index": link.rendered_span.segment_index if link.rendered_span else None,
        "rendered_char_start": link.rendered_span.char_start if link.rendered_span else None,
        "rendered_char_end": link.rendered_span.char_end if link.rendered_span else None,
        "valid_at_start": valid_start.isoformat() if valid_start else None,
        "valid_at_end": valid_end.isoformat() if valid_end else None,
        "detail_json": link.detail_json,
    }


INTERLINK_ROW_COLUMNS = tuple(legal_interlink_to_row(
    LegalInterlink(
        interlink_id="schema",
        source_work=LegalWorkRef("zz", "schema", "source"),
        source_locator=None,
        source_span=None,
        rendered_span=None,
        surface_text="schema",
        surface_kind=InterlinkSurfaceKind.METADATA_REF,
        target=InterlinkTarget(work=LegalWorkRef("zz", "schema", "target")),
        role=InterlinkRole.UNKNOWN,
        resolution_status=InterlinkResolutionStatus.RESOLVED,
        confidence=InterlinkConfidence.EXACT,
        resolver_id="schema",
    )
).keys())

