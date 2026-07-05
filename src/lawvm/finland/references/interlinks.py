"""Finland adapters from local citation records to neutral interlink rows."""
from __future__ import annotations

import json
import re
from typing import Optional, Tuple

from lawvm.core.interlinks import (
    InterlinkConfidence,
    InterlinkResolutionStatus,
    InterlinkRole,
    InterlinkSourceSpan,
    InterlinkSurfaceKind,
    InterlinkTarget,
    LegalInterlink,
    LegalLocatorRef,
    LegalWorkRef,
    RenderedTextSpan,
)
from lawvm.core.locator import HierarchicalLocator, LocatorSegment

_AKN_LOCATOR_PARTS = {
    "part": "part",
    "chp": "chapter",
    "sec": "section",
    "subsec": "subsection",
    "para": "paragraph",
    "subpara": "subparagraph",
}
_AKN_LOCATOR_PART_RE = re.compile(
    r"^(part|chp|sec|subsec|para|subpara)_([A-Za-z0-9.-]{1,40})$",
    re.IGNORECASE,
)

# Placeholder prefix an UNRESOLVED-by-identity EU-by-nickname mention carries on
# its target (``eu-nickname:<surface>``): the by-name / eu_directive recognizer
# lanes type an EU instrument named only by a Finnish-shaped nickname with this
# prefix and DEFER the CELEX pick. When the nickname is genuinely AMBIGUOUS the
# registry already computed the FULL small candidate CELEX set but the flat
# interlink projection discards it — the mention reaches this consumer with an
# ``eu-nickname:`` placeholder target and no single pick. This consumer recovers
# that set (a pure registry READ, never a pick) so a small discrete ambiguity is
# surfaced as a one-of-K DISAMBIGUATION link instead of being dropped.
_EU_NICKNAME_PLACEHOLDER_PREFIX = "eu-nickname:"

# The largest candidate set we surface as a disambiguation link. A SMALL discrete
# alternative set ("one of these K acts") is far more useful than a dropped cite;
# a larger set is not a helpful disambiguation and stays unlinked (as today), and
# an OPEN/vague reference names no candidate set at all (also unchanged). Every
# candidate is a POSSIBILITY, never a resolved fact — the link is stamped
# AMBIGUOUS + HEURISTIC, never a definite EXACT single-target link.
_MAX_DISAMBIGUATION_CANDIDATES = 4


def _eu_nickname_disambiguation_candidates(target_statute_id: str) -> Tuple[str, ...]:
    """Recover the small discrete CELEX candidate set for an ambiguous EU nickname.

    ``target_statute_id`` is the mention's target id. When it is an
    ``eu-nickname:<surface>`` placeholder, the surface is looked up in the EU
    nickname registry (the SAME read ``references.resolve`` performs — this is a
    pure registry READ, it invents nothing and picks nothing). Returns the
    candidate CELEX work ids (``celex:<CELEX>``) when the nickname is genuinely
    AMBIGUOUS (registry ``multiple``) AND the set is SMALL and discrete
    (``<= _MAX_DISAMBIGUATION_CANDIDATES``); otherwise the empty tuple (not an
    ``eu-nickname:`` placeholder, a single/none registry result, or a set too
    large to be a useful disambiguation — all stay unlinked exactly as before).
    """
    if not target_statute_id.startswith(_EU_NICKNAME_PLACEHOLDER_PREFIX):
        return ()
    surface = target_statute_id[len(_EU_NICKNAME_PLACEHOLDER_PREFIX) :]
    if not surface:
        return ()
    from lawvm.finland.references.registries import eu_nickname

    result = eu_nickname.lookup(surface)
    if result.registry_status is not eu_nickname.RegistryStatus.MULTIPLE:
        return ()
    candidates = tuple(f"celex:{celex}" for celex in result.candidates)
    if not candidates or len(candidates) > _MAX_DISAMBIGUATION_CANDIDATES:
        return ()
    return candidates


def fi_work_ref(local_id: str, work_kind: str = "normative_act") -> LegalWorkRef:
    return LegalWorkRef("fi", work_kind, local_id, f"fi:{work_kind}:{local_id}")


def eu_work_ref(local_id: str) -> LegalWorkRef:
    return LegalWorkRef("eu", "eu_act", local_id, f"eu:eu_act:{local_id}")


def fi_work_ref_from_canonical_id(canonical_id: str) -> Optional[LegalWorkRef]:
    value = str(canonical_id or "").strip()
    if not value:
        return None
    if value.startswith("he/"):
        return LegalWorkRef("fi", "government_proposal", value, f"fi:government_proposal:{value}")
    if value.startswith("eu/"):
        return eu_work_ref(value)
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
    return fi_work_ref(value)


def _locator_from_reference_provision(provision_ref: object) -> Optional[LegalLocatorRef]:
    section_label = str(getattr(provision_ref, "section_label", "") or "")
    subsection_num = getattr(provision_ref, "subsection_num", None)
    item_label = getattr(provision_ref, "item_label", None)
    provision_path = str(getattr(provision_ref, "provision_path", "") or "")
    akn_locator = _locator_from_akn_provision_path(provision_path)
    if akn_locator is not None:
        return LegalLocatorRef(
            locator=akn_locator,
            raw_locator=provision_path,
            resolver_namespace="fi.akn_eid",
        )
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


def _locator_from_akn_provision_path(provision_path: str) -> Optional[HierarchicalLocator]:
    if "_" not in provision_path:
        return None
    segments: list[LocatorSegment] = []
    for raw_part in provision_path.split("__"):
        match = _AKN_LOCATOR_PART_RE.fullmatch(raw_part)
        if match is None:
            return None
        kind = _AKN_LOCATOR_PARTS[match.group(1).lower()]
        segments.append(LocatorSegment(kind, match.group(2)))
    return HierarchicalLocator(tuple(segments)) if segments else None


def _reference_mention_detail_json(
    *,
    source_locator: Optional[LegalLocatorRef],
    target_locator: Optional[LegalLocatorRef],
) -> str:
    detail: dict[str, str] = {}
    if source_locator is not None and source_locator.raw_locator:
        detail["source_raw_locator"] = source_locator.raw_locator
        if source_locator.resolver_namespace:
            detail["source_locator_resolver"] = source_locator.resolver_namespace
    if target_locator is not None and target_locator.raw_locator:
        detail["target_raw_locator"] = target_locator.raw_locator
        if target_locator.resolver_namespace:
            detail["target_locator_resolver"] = target_locator.resolver_namespace
    return json.dumps(detail, ensure_ascii=False, sort_keys=True) if detail else "{}"


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


def fi_interlink_from_reference_mention(
    mention: object,
    *,
    interlink_id: str,
    surface_text: str | None = None,
    rendered_span: RenderedTextSpan | None = None,
) -> LegalInterlink:
    """Adapt a Finnish ReferenceMention-like object into the neutral contract."""
    from lawvm.core.reference_mention import CiteConfidence, CiteKind

    src = getattr(mention, "source_provision_ref", None)
    tgt = getattr(mention, "target_provision_ref", None)
    phrase_lemma = str(getattr(mention, "phrase_lemma", "") or "")
    edge_subtype = str(getattr(mention, "edge_subtype", "") or "")
    cite_kind = getattr(mention, "cite_kind", None)
    cite_confidence = getattr(mention, "cite_confidence", None)

    source_statute_id = str(getattr(src, "statute_id", "") or "")
    source_work = fi_work_ref(source_statute_id)
    target_statute_id = str(getattr(tgt, "statute_id", "") or "") if tgt is not None else ""
    target_work = fi_work_ref_from_canonical_id(target_statute_id) if tgt is not None else None
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

    # AMBIGUOUS-but-resolvable: an EU-by-nickname cite whose nickname the registry
    # maps to a SMALL discrete CELEX set (the registry refuses to pick one). The
    # flat mention carries an ``eu-nickname:<surface>`` placeholder target and no
    # single pick; recovering the candidate set here surfaces it as a one-of-K
    # DISAMBIGUATION link ("one of: A, B, …") instead of the current dropped/garbage
    # placeholder target. This ONLY changes the ambiguous-small-discrete case: an
    # EXACT single-target cite (below) and an OPEN/vague/unresolved cite (no
    # ``eu-nickname:`` placeholder or no candidate set) are byte-unchanged.
    candidate_work_ids: Tuple[str, ...] = ()
    if cite_confidence == CiteConfidence.AMBIGUOUS:
        candidate_work_ids = _eu_nickname_disambiguation_candidates(target_statute_id)
        if candidate_work_ids:
            # A one-of-K possibility set, never a resolved single: drop the
            # placeholder single-target work (do not launder one candidate into a
            # definite target) and carry the whole set. Status stays AMBIGUOUS and
            # confidence HEURISTIC — this is a disambiguation POSSIBILITY, not an
            # EXACT link.
            target_work = None
            status = InterlinkResolutionStatus.AMBIGUOUS
            confidence = InterlinkConfidence.HEURISTIC

    owned_surface_text = str(getattr(mention, "surface_text", "") or "")
    source_locator = _locator_from_reference_provision(src)
    target_locator = _locator_from_reference_provision(tgt) if tgt is not None else None

    return LegalInterlink(
        interlink_id=interlink_id,
        source_work=source_work,
        source_locator=source_locator,
        source_span=_source_span_from_reference_span(getattr(mention, "source_span", None)),
        rendered_span=rendered_span,
        surface_text=surface_text or owned_surface_text or phrase_lemma or edge_subtype or "reference",
        surface_kind=surface_kind,
        target=InterlinkTarget(
            work=target_work,
            locator=target_locator,
            candidate_work_ids=candidate_work_ids,
        ),
        role=role,
        resolution_status=status,
        confidence=confidence,
        resolver_id="fi.reference_mention",
        valid_at_interval=getattr(mention, "valid_at_interval", (None, None)),
        detail_json=_reference_mention_detail_json(
            source_locator=source_locator,
            target_locator=target_locator,
        ),
    )


def fi_interlink_from_inline_citation(
    citation: object,
    *,
    interlink_id: str,
    rendered_span: RenderedTextSpan | None = None,
) -> LegalInterlink:
    """Adapt a Finnish InlineCitation-like object into the neutral contract."""
    from lawvm.core.inline_citation import InlineCitationKind

    source_doc_id = str(getattr(citation, "source_doc_id", "") or "")
    source_doc_kind = str(getattr(citation, "source_doc_kind", "") or "")
    kind = getattr(citation, "kind", None)
    canonical_id = str(getattr(citation, "canonical_id", "") or "")

    source_work_kind = "government_proposal" if source_doc_kind == "he" else "normative_act"
    target_work = fi_work_ref_from_canonical_id(canonical_id) if canonical_id else None
    status = InterlinkResolutionStatus.RESOLVED if target_work is not None else InterlinkResolutionStatus.UNRESOLVED
    if kind in (
        InlineCitationKind.COURT_KKO,
        InlineCitationKind.COURT_KHO,
        InlineCitationKind.OMBUDSMAN_EOA,
        InlineCitationKind.CHANCELLOR_OKA,
    ):
        status = InterlinkResolutionStatus.EXTERNAL_ONLY if target_work is not None else status

    source_locator_text = str(getattr(citation, "source_provision_ref", "") or "")
    return LegalInterlink(
        interlink_id=interlink_id,
        source_work=fi_work_ref(source_doc_id, source_work_kind),
        source_locator=LegalLocatorRef(raw_locator=source_locator_text, resolver_namespace="fi.inline_citation") if source_locator_text else None,
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


def fi_interlink_from_preparatory_reference(
    ref: object,
    *,
    interlink_id: str,
    rendered_span: RenderedTextSpan | None = None,
) -> LegalInterlink:
    """Adapt a Finnish PreparatoryReference-like object into the neutral contract."""
    from lawvm.core.preparatory_reference import PreparatoryReferenceConfidence

    source_statute_id = str(getattr(ref, "source_statute_id", "") or "")
    canonical_id = str(getattr(ref, "canonical_id", "") or "")
    target_work = fi_work_ref_from_canonical_id(canonical_id) if canonical_id else None
    confidence_value = getattr(ref, "confidence", None)
    status = InterlinkResolutionStatus.RESOLVED if target_work is not None else InterlinkResolutionStatus.UNRESOLVED
    confidence = InterlinkConfidence.EXACT if confidence_value == PreparatoryReferenceConfidence.EXACT else InterlinkConfidence.HEURISTIC
    if confidence_value == PreparatoryReferenceConfidence.UNRESOLVED:
        confidence = InterlinkConfidence.LEGACY_UNKNOWN

    return LegalInterlink(
        interlink_id=interlink_id,
        source_work=fi_work_ref(source_statute_id),
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
