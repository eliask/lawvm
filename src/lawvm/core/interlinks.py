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

from lawvm.core.locator import HierarchicalLocator, LocatorSegment


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


def _fi_work_ref(local_id: str, work_kind: str = "normative_act") -> LegalWorkRef:
    return LegalWorkRef("fi", work_kind, local_id, f"fi:{work_kind}:{local_id}")


def _eu_work_ref(local_id: str) -> LegalWorkRef:
    return LegalWorkRef("eu", "eu_act", local_id, f"eu:eu_act:{local_id}")


def _work_ref_from_canonical_id(canonical_id: str, *, default_jurisdiction: str = "fi") -> Optional[LegalWorkRef]:
    value = str(canonical_id or "").strip()
    if not value:
        return None
    if value.startswith("he/"):
        return LegalWorkRef(default_jurisdiction, "government_proposal", value, f"{default_jurisdiction}:government_proposal:{value}")
    if value.startswith("eu/"):
        return _eu_work_ref(value)
    if value.startswith("fi.court."):
        return LegalWorkRef("fi", "court_decision", value, value)
    if value.startswith("fi.eoa.") or value.startswith("fi.oka."):
        return LegalWorkRef("fi", "oversight_decision", value, value)
    if value.startswith("fi.vtv.") or value.startswith("fi.wgm."):
        return LegalWorkRef("fi", "report", value, value)
    if value.startswith("fi.ek.") or value.startswith("fi.ev.") or value.startswith("fi.evk.") or value.startswith("fi.la."):
        return LegalWorkRef("fi", "parliamentary_document", value, value)
    if value.startswith("fi.committee"):
        return LegalWorkRef("fi", "committee_document", value, value)
    return LegalWorkRef(default_jurisdiction, "normative_act", value, f"{default_jurisdiction}:normative_act:{value}")


def _locator_from_reference_provision(provision_ref: object) -> Optional[LegalLocatorRef]:
    section_label = str(getattr(provision_ref, "section_label", "") or "")
    subsection_num = getattr(provision_ref, "subsection_num", None)
    item_label = getattr(provision_ref, "item_label", None)
    provision_path = str(getattr(provision_ref, "provision_path", "") or "")
    segments: list[LocatorSegment] = []
    if section_label:
        segments.append(LocatorSegment("section", section_label))
    if subsection_num is not None:
        segments.append(LocatorSegment("subsection", str(subsection_num)))
    if item_label:
        segments.append(LocatorSegment("item", str(item_label)))
    if segments:
        return LegalLocatorRef(
            locator=HierarchicalLocator(tuple(segments)),
            raw_locator=provision_path,
            resolver_namespace="fi.provision_ref",
        )
    if provision_path:
        return LegalLocatorRef(raw_locator=provision_path, resolver_namespace="fi.provision_ref")
    return None


def _source_span_from_reference_span(span: object | None) -> Optional[InterlinkSourceSpan]:
    if span is None:
        return None
    source_artifact_id = str(getattr(span, "source_file", "") or "")
    if not source_artifact_id:
        return None
    return InterlinkSourceSpan(
        source_artifact_id=source_artifact_id,
        byte_offset=getattr(span, "byte_offset", None),
        byte_len=getattr(span, "byte_len", None),
    )


def _source_span_from_flat_fields(
    source_artifact_id: str | None,
    byte_offset: int | None,
    byte_len: int | None,
) -> Optional[InterlinkSourceSpan]:
    if not source_artifact_id:
        return None
    return InterlinkSourceSpan(
        source_artifact_id=source_artifact_id,
        byte_offset=byte_offset,
        byte_len=byte_len,
    )


def interlink_from_reference_mention(
    mention: object,
    *,
    interlink_id: str,
    jurisdiction: str = "fi",
    surface_text: str | None = None,
    rendered_span: RenderedTextSpan | None = None,
) -> LegalInterlink:
    """Adapt a ReferenceMention-like object into the neutral interlink contract."""
    from lawvm.core.reference_mention import CiteConfidence, CiteKind

    src = getattr(mention, "source_provision_ref")
    tgt = getattr(mention, "target_provision_ref")
    phrase_lemma = str(getattr(mention, "phrase_lemma", "") or "")
    edge_subtype = str(getattr(mention, "edge_subtype", "") or "")
    cite_kind = getattr(mention, "cite_kind")
    cite_confidence = getattr(mention, "cite_confidence")

    source_work = LegalWorkRef(
        jurisdiction,
        "normative_act",
        str(getattr(src, "statute_id", "") or ""),
        f"{jurisdiction}:normative_act:{getattr(src, 'statute_id', '')}",
    )
    target_work = _work_ref_from_canonical_id(str(getattr(tgt, "statute_id", "") or ""), default_jurisdiction=jurisdiction) if tgt is not None else None
    if cite_kind == CiteKind.INTERNAL and target_work is None:
        target_work = source_work

    role = InterlinkRole.CITES
    if edge_subtype == "REPEALS":
        role = InterlinkRole.REPEALS
    elif edge_subtype == "ISSUED_UNDER":
        role = InterlinkRole.ISSUED_UNDER
    elif edge_subtype == "ISSUES":
        role = InterlinkRole.AUTHORITY

    surface_kind = InterlinkSurfaceKind.PROSE_REF
    if phrase_lemma == "ref_element":
        surface_kind = InterlinkSurfaceKind.XML_REF
    elif edge_subtype in {"REPEALS", "ISSUED_UNDER", "ISSUES"}:
        surface_kind = InterlinkSurfaceKind.METADATA_REF

    status = InterlinkResolutionStatus.RESOLVED if target_work is not None else InterlinkResolutionStatus.UNRESOLVED
    if cite_confidence == CiteConfidence.UNRESOLVED:
        status = InterlinkResolutionStatus.UNRESOLVED
    elif cite_confidence == CiteConfidence.AMBIGUOUS:
        status = InterlinkResolutionStatus.AMBIGUOUS
    elif cite_confidence == CiteConfidence.BROKEN:
        status = InterlinkResolutionStatus.BROKEN
    elif cite_kind == CiteKind.EU and target_work is not None:
        status = InterlinkResolutionStatus.EXTERNAL_ONLY

    confidence = InterlinkConfidence.EXACT
    if cite_confidence == CiteConfidence.APPROXIMATE:
        confidence = InterlinkConfidence.HEURISTIC
    elif cite_confidence == CiteConfidence.UNRESOLVED:
        confidence = InterlinkConfidence.LEGACY_UNKNOWN
    if status == InterlinkResolutionStatus.UNRESOLVED and target_work is None:
        confidence = InterlinkConfidence.LEGACY_UNKNOWN

    return LegalInterlink(
        interlink_id=interlink_id,
        source_work=source_work,
        source_locator=_locator_from_reference_provision(src),
        source_span=_source_span_from_reference_span(getattr(mention, "source_span", None)),
        rendered_span=rendered_span,
        surface_text=surface_text or phrase_lemma or edge_subtype or "reference",
        surface_kind=surface_kind,
        target=InterlinkTarget(work=target_work, locator=_locator_from_reference_provision(tgt) if tgt is not None else None),
        role=role,
        resolution_status=status,
        confidence=confidence,
        resolver_id=f"{jurisdiction}.reference_mention",
        valid_at_interval=getattr(mention, "valid_at_interval", (None, None)),
    )


def interlink_from_inline_citation(
    citation: object,
    *,
    interlink_id: str,
    rendered_span: RenderedTextSpan | None = None,
) -> LegalInterlink:
    """Adapt an InlineCitation-like object into the neutral interlink contract."""
    from lawvm.core.inline_citation import InlineCitationKind

    source_doc_id = str(getattr(citation, "source_doc_id", "") or "")
    source_doc_kind = str(getattr(citation, "source_doc_kind", "") or "")
    kind = getattr(citation, "kind")
    canonical_id = str(getattr(citation, "canonical_id", "") or "")

    source_work_kind = "government_proposal" if source_doc_kind == "he" else "normative_act"
    target_work = _work_ref_from_canonical_id(canonical_id) if canonical_id else None
    status = InterlinkResolutionStatus.RESOLVED if target_work is not None else InterlinkResolutionStatus.UNRESOLVED
    if kind in (InlineCitationKind.COURT_KKO, InlineCitationKind.COURT_KHO, InlineCitationKind.OMBUDSMAN_EOA, InlineCitationKind.CHANCELLOR_OKA):
        status = InterlinkResolutionStatus.EXTERNAL_ONLY if target_work is not None else status

    return LegalInterlink(
        interlink_id=interlink_id,
        source_work=LegalWorkRef("fi", source_work_kind, source_doc_id, f"fi:{source_work_kind}:{source_doc_id}"),
        source_locator=LegalLocatorRef(raw_locator=str(getattr(citation, "source_provision_ref", "") or ""), resolver_namespace="fi.inline_citation") if getattr(citation, "source_provision_ref", "") else None,
        source_span=_source_span_from_flat_fields(
            getattr(citation, "source_span_file", None),
            getattr(citation, "source_span_byte_offset", None),
            getattr(citation, "source_span_byte_len", None),
        ),
        rendered_span=rendered_span,
        surface_text=str(getattr(citation, "raw_text", "") or ""),
        surface_kind=InterlinkSurfaceKind.PROSE_REF,
        target=InterlinkTarget(work=target_work),
        role=InterlinkRole.CITES,
        resolution_status=status,
        confidence=InterlinkConfidence.EXACT if target_work is not None else InterlinkConfidence.LEGACY_UNKNOWN,
        resolver_id="fi.inline_citation",
    )


def interlink_from_preparatory_reference(
    ref: object,
    *,
    interlink_id: str,
    rendered_span: RenderedTextSpan | None = None,
) -> LegalInterlink:
    """Adapt a PreparatoryReference-like object into the neutral interlink contract."""
    from lawvm.core.preparatory_reference import PreparatoryReferenceConfidence

    source_statute_id = str(getattr(ref, "source_statute_id", "") or "")
    canonical_id = str(getattr(ref, "canonical_id", "") or "")
    target_work = _work_ref_from_canonical_id(canonical_id) if canonical_id else None
    confidence_value = getattr(ref, "confidence")
    status = InterlinkResolutionStatus.RESOLVED if target_work is not None else InterlinkResolutionStatus.UNRESOLVED
    confidence = InterlinkConfidence.EXACT if confidence_value == PreparatoryReferenceConfidence.EXACT else InterlinkConfidence.HEURISTIC
    if confidence_value == PreparatoryReferenceConfidence.UNRESOLVED:
        confidence = InterlinkConfidence.LEGACY_UNKNOWN

    return LegalInterlink(
        interlink_id=interlink_id,
        source_work=_fi_work_ref(source_statute_id),
        source_locator=None,
        source_span=_source_span_from_flat_fields(
            getattr(ref, "source_span_file", None),
            getattr(ref, "source_span_byte_offset", None),
            getattr(ref, "source_span_byte_len", None),
        ),
        rendered_span=rendered_span,
        surface_text=str(getattr(ref, "raw_text", "") or ""),
        surface_kind=InterlinkSurfaceKind.PREPARATORY_REF,
        target=InterlinkTarget(work=target_work),
        role=InterlinkRole.PREPARATORY_HISTORY,
        resolution_status=status,
        confidence=confidence,
        resolver_id="fi.preparatory_reference",
        valid_at_interval=getattr(ref, "valid_at_interval", (None, None)),
    )
