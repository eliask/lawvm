from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence

import lxml.etree as etree

from lawvm.core.compile_result import StrictProfile
from lawvm.core.source_lane import SourceLaneAttempt, SourceLaneSelectionEvidence
from lawvm.finland.citation_routing import (
    OP_KEYWORDS,
    extract_pending_amendment_target_id,
    route_amendment,
)
from lawvm.finland.metadata import (
    _normalize_johtolause_verbs,
    get_johtolause,
    get_operative_body_repeal_candidate,
)
from lawvm.finland.scope import restrict_sec1_fallback_to_parent

_OPERATIVE_BODY_TAGS = {
    "section",
    "chapter",
    "part",
    "article",
    "subsection",
    "paragraph",
    "point",
    "subparagraph",
    "table",
    "blocklist",
    "item",
}


@dataclass(frozen=True)
class OperativeLaneCandidate:
    lane: str
    raw_text: str
    normalized_text: str
    usable: bool
    selected: bool
    reason: str


@dataclass(frozen=True)
class OperativeLaneDecision:
    selected_lane: str
    chosen_operative_text: str
    chosen_normalized_text: str
    should_apply: bool
    route_reason: str
    pre_routing_sec1_requested: bool
    pre_routing_sec1_applied: bool
    post_routing_sec1_applied: bool
    body_repeal_candidate_used: bool
    citation_guard_johto: str
    citation_guard_sec1: str
    route_target_amendment_id: str


@dataclass(frozen=True)
class AcquisitionDiagnostic:
    rule_id: str
    family: str
    phase: str
    reason: str
    lane: str
    strict_profile: str
    blocking: bool
    strict_disposition: str
    quirks_disposition: str


@dataclass(frozen=True)
class AmendmentAcquisitionResult:
    preamble_text: str
    preamble_normalized: str
    sec1_text: str
    sec1_normalized: str
    body_lead_text: str
    body_lead_normalized: str
    body_repeal_candidate: str
    body_repeal_candidate_normalized: str
    lacks_operative_structure: bool
    operative_structure_tags: tuple[str, ...]
    candidates: tuple[OperativeLaneCandidate, ...]
    rejected_lanes: tuple[tuple[str, str], ...]
    diagnostics: tuple[AcquisitionDiagnostic, ...]
    decision: OperativeLaneDecision


def operative_lane_selection_evidence(result: AmendmentAcquisitionResult) -> dict[str, object]:
    """Project operative-text lane selection through the shared source-lane carrier."""

    selected_attempt_lane = next((candidate.lane for candidate in result.candidates if candidate.selected), "")
    selection_detail = {
        "should_apply": result.decision.should_apply,
        "route_target_amendment_id": result.decision.route_target_amendment_id,
        "pre_routing_sec1_requested": result.decision.pre_routing_sec1_requested,
        "pre_routing_sec1_applied": result.decision.pre_routing_sec1_applied,
        "post_routing_sec1_applied": result.decision.post_routing_sec1_applied,
        "body_repeal_candidate_used": result.decision.body_repeal_candidate_used,
    }
    if selected_attempt_lane and selected_attempt_lane != result.decision.selected_lane:
        selection_detail["selected_lane_route_from"] = selected_attempt_lane
        selection_detail["selected_lane_routing_rule"] = (
            result.decision.route_reason or "operative lane routing"
        )
    attempts = tuple(
        SourceLaneAttempt(
            lane=candidate.lane,
            status="selected" if candidate.selected else candidate.reason or "not_selected",
            detail={
                "usable": candidate.usable,
                "raw_text_length": len(candidate.raw_text),
                "normalized_text_length": len(candidate.normalized_text),
            },
        )
        for candidate in result.candidates
    )
    return SourceLaneSelectionEvidence(
        rule_id="fi_acquisition_operative_text_lane_selected",
        phase="acquisition",
        reason=result.decision.route_reason or "operative text lane selected",
        selected_lane=result.decision.selected_lane,
        attempts=attempts,
        blocking=False,
        strict_disposition="record",
        quirks_disposition="record",
        detail=selection_detail,
    ).to_diagnostic_detail()


def _localname(node: etree._Element) -> str:
    return node.tag.rsplit("}", 1)[-1] if isinstance(node.tag, str) else ""


def amendment_operative_structure_tags(tree: etree._Element) -> list[str]:
    body = tree.find(".//{*}body")
    root = body if body is not None else tree
    found: list[str] = []
    seen: set[str] = set()
    for node in root.iter():
        tag = _localname(node)
        if tag in _OPERATIVE_BODY_TAGS and tag not in seen:
            seen.add(tag)
            found.append(tag)
    return found


def amendment_lacks_operative_structure(tree: etree._Element) -> tuple[bool, list[str]]:
    tags = amendment_operative_structure_tags(tree)
    return (len(tags) == 0, tags)


def should_use_sec1_fallback_pre_routing(johto: Optional[str]) -> bool:
    return not johto or len(johto) < 50


def should_use_sec1_fallback_post_routing(johto: str, sec1_text: str) -> bool:
    if any(kw in johto.lower() for kw in OP_KEYWORDS):
        return False
    has_subprov = re.search(
        r"§:?n?\s+(?:\d[\d.]*\s+)?(?:kohta|kohdan|momentti|momentin|johdantokappale)",
        sec1_text.lower(),
    )
    pure_repeal_subprov = (
        has_subprov
        and "kumotaan" in sec1_text.lower()
        and not re.search(r"\b(muutetaan|lisätään|korvataan|otetaan)\b", sec1_text.lower())
    )
    return bool(any(kw in sec1_text.lower() for kw in OP_KEYWORDS) and (not has_subprov or pure_repeal_subprov))


def _extract_sec1_text(muutos_tree: etree._Element, parent_id: str) -> str:
    sec1_el = muutos_tree.find(".//{*}section[@eId='sec_1']")
    if sec1_el is None:
        return ""
    sec1_text = etree.tostring(sec1_el, method="text", encoding="unicode").strip()
    sec1_text = re.sub(r"^\d+\s*[a-zäöå]?\s*§\s*", "", sec1_text).strip()
    return restrict_sec1_fallback_to_parent(sec1_text, parent_id)


def _extract_body_lead_text(muutos_tree: etree._Element) -> str:
    """Extract operative text from the first unnumbered body lead section.

    Some Finnish amendment acts keep the ceremonial johtolause in the preamble
    and place the real operative clause in the first unnumbered section under
    ``statuteProvisionsWrapper``. This helper extracts that clause verbatim
    without the sec1 parent-narrowing logic, so multi-verb chains remain intact.
    """
    body = muutos_tree.find(".//{*}body")
    if body is None:
        return ""

    for node in body.iter():
        if _localname(node) != "section":
            continue
        num_text = (node.findtext("{*}num") or "").strip()
        if num_text:
            continue
        lead_text = etree.tostring(node, method="text", encoding="unicode").strip()
        if any(kw in lead_text.lower() for kw in OP_KEYWORDS):
            return lead_text
    return ""


def build_amendment_acquisition_result(
    *,
    xml_bytes: bytes,
    parent_id: str,
    amendment_id: str,
    source_title: str,
    parent_title: str,
    strict_profile: Optional[StrictProfile] = None,
    lacks_operative_structure: Optional[bool] = None,
    operative_structure_tags: Optional[Sequence[str]] = None,
) -> AmendmentAcquisitionResult:
    muutos_tree = etree.fromstring(xml_bytes)
    if lacks_operative_structure is None or operative_structure_tags is None:
        lacks_operative_structure, operative_structure_tags = amendment_lacks_operative_structure(muutos_tree)

    preamble_text = get_johtolause(xml_bytes)
    preamble_normalized = _normalize_johtolause_verbs(preamble_text or "")
    sec1_text = _extract_sec1_text(muutos_tree, parent_id)
    sec1_normalized = _normalize_johtolause_verbs(sec1_text) if sec1_text else ""
    body_lead_text = _extract_body_lead_text(muutos_tree)
    body_lead_normalized = _normalize_johtolause_verbs(body_lead_text) if body_lead_text else ""

    body_repeal_candidate = ""
    if lacks_operative_structure:
        body_repeal_candidate = get_operative_body_repeal_candidate(xml_bytes)
    body_repeal_candidate_normalized = _normalize_johtolause_verbs(body_repeal_candidate) if body_repeal_candidate else ""

    pre_routing_sec1_requested = bool(should_use_sec1_fallback_pre_routing(preamble_text) and sec1_text)
    allows_context_dependent_anchor_resolution = (
        strict_profile is None or strict_profile.allows_context_dependent_anchor_resolution
    )
    pre_routing_sec1_applied = bool(
        pre_routing_sec1_requested
        and allows_context_dependent_anchor_resolution
    )
    pre_routing_sec1_blocked = bool(pre_routing_sec1_requested and not allows_context_dependent_anchor_resolution)
    body_lead_pre_routing_requested = bool(
        not pre_routing_sec1_applied
        and body_lead_text
        and not any(kw in (preamble_text or "").lower() for kw in OP_KEYWORDS)
    )
    body_lead_pre_routing_applied = bool(
        body_lead_pre_routing_requested
        and allows_context_dependent_anchor_resolution
    )
    body_lead_pre_routing_blocked = bool(
        body_lead_pre_routing_requested and not allows_context_dependent_anchor_resolution
    )

    working_text = preamble_text
    body_repeal_candidate_used = False
    if pre_routing_sec1_applied:
        working_text = sec1_text
    elif body_lead_pre_routing_applied:
        working_text = body_lead_text
    elif not any(kw in (working_text or "").lower() for kw in OP_KEYWORDS) and body_repeal_candidate:
        working_text = body_repeal_candidate
        body_repeal_candidate_used = True

    citation_guard_johto = _normalize_johtolause_verbs(working_text or "")
    citation_guard_sec1 = ""
    if not pre_routing_sec1_applied and sec1_text and allows_context_dependent_anchor_resolution:
        citation_guard_sec1 = sec1_normalized

    working_normalized = _normalize_johtolause_verbs(working_text or "")
    should_apply, route_reason = route_amendment(
        citation_guard_johto=citation_guard_johto,
        citation_guard_sec1=citation_guard_sec1,
        johto=working_normalized,
        parent_id=parent_id,
        amendment_id=amendment_id,
        source_title=source_title,
        parent_title=parent_title,
    )
    route_target_amendment_id = ""
    if str(route_reason or "") == "pending_amendment_of_parent_skip":
        route_target_amendment_id = (
            extract_pending_amendment_target_id(
                preamble_text or working_text,
                amendment_id,
                source_title,
                parent_title,
            )
            or ""
        )

    post_routing_sec1_requested = bool(
        should_apply
        and sec1_text
        and should_use_sec1_fallback_post_routing(working_normalized, sec1_normalized)
    )
    post_routing_sec1_applied = bool(
        post_routing_sec1_requested
        and allows_context_dependent_anchor_resolution
    )
    post_routing_sec1_blocked = bool(
        post_routing_sec1_requested and not allows_context_dependent_anchor_resolution
    )
    if post_routing_sec1_applied:
        working_text = sec1_text
        working_normalized = sec1_normalized

    if pre_routing_sec1_applied:
        selected_lane = "sec1_fallback_pre_routing"
    elif body_lead_pre_routing_applied:
        selected_lane = "body_lead_fallback_pre_routing"
    elif post_routing_sec1_applied:
        selected_lane = "sec1_fallback_post_routing"
    elif body_repeal_candidate_used:
        selected_lane = "body_repeal_candidate"
    else:
        selected_lane = "preamble"

    selected_reason_map = {
        "preamble": "selected_as_primary_preamble_lane",
        "sec1_fallback_pre_routing": "preamble_missing_or_too_short",
        "body_lead_fallback_pre_routing": "preamble_ceremonial_body_lead_selected",
        "sec1_fallback_post_routing": "preamble_not_operative_after_routing",
        "body_repeal_candidate": "body_repeal_candidate_selected",
    }
    selected_reason = selected_reason_map[selected_lane]
    strict_block_reason = "strict_profile_blocked_context_dependent_anchor_resolution"
    diagnostics: list[AcquisitionDiagnostic] = []
    if pre_routing_sec1_blocked:
        diagnostics.append(
            AcquisitionDiagnostic(
                rule_id="ACQ.OPERATIVE_LANE_STRICT_BLOCKED",
                family="target_resolution_recovery",
                phase="acquisition",
                reason="strict profile blocked context-dependent section 1 operative fallback",
                lane="sec1_fallback_pre_routing",
                strict_profile=strict_profile.name if strict_profile is not None else "",
                blocking=True,
                strict_disposition="block",
                quirks_disposition="record",
            )
        )
    if post_routing_sec1_blocked:
        diagnostics.append(
            AcquisitionDiagnostic(
                rule_id="ACQ.OPERATIVE_LANE_STRICT_BLOCKED",
                family="target_resolution_recovery",
                phase="acquisition",
                reason="strict profile blocked context-dependent section 1 operative fallback after routing",
                lane="sec1_fallback_post_routing",
                strict_profile=strict_profile.name if strict_profile is not None else "",
                blocking=True,
                strict_disposition="block",
                quirks_disposition="record",
            )
        )
    if body_lead_pre_routing_blocked:
        diagnostics.append(
            AcquisitionDiagnostic(
                rule_id="ACQ.OPERATIVE_LANE_STRICT_BLOCKED",
                family="target_resolution_recovery",
                phase="acquisition",
                reason="strict profile blocked context-dependent body lead operative fallback",
                lane="body_lead_fallback_pre_routing",
                strict_profile=strict_profile.name if strict_profile is not None else "",
                blocking=True,
                strict_disposition="block",
                quirks_disposition="record",
            )
        )

    candidates = [
        OperativeLaneCandidate(
            lane="preamble",
            raw_text=preamble_text,
            normalized_text=preamble_normalized,
            usable=bool(preamble_text),
            selected=selected_lane == "preamble",
            reason=selected_reason if selected_lane == "preamble" else "not_selected",
        )
    ]
    if sec1_text:
        candidates.append(
            OperativeLaneCandidate(
                lane="sec1_fallback",
                raw_text=sec1_text,
                normalized_text=sec1_normalized,
                usable=bool(sec1_text),
                selected=selected_lane.startswith("sec1_fallback"),
                reason=selected_reason if selected_lane.startswith("sec1_fallback") else (
                    strict_block_reason if pre_routing_sec1_blocked or post_routing_sec1_blocked else "not_selected"
                ),
            )
        )
    if body_lead_text:
        candidates.append(
            OperativeLaneCandidate(
                lane="body_lead_fallback",
                raw_text=body_lead_text,
                normalized_text=body_lead_normalized,
                usable=bool(body_lead_text),
                selected=selected_lane == "body_lead_fallback_pre_routing",
                reason=selected_reason if selected_lane == "body_lead_fallback_pre_routing" else (
                    strict_block_reason if body_lead_pre_routing_blocked else "not_selected"
                ),
            )
        )
    if body_repeal_candidate:
        candidates.append(
            OperativeLaneCandidate(
                lane="body_repeal_candidate",
                raw_text=body_repeal_candidate,
                normalized_text=body_repeal_candidate_normalized,
                usable=bool(body_repeal_candidate),
                selected=selected_lane == "body_repeal_candidate",
                reason=selected_reason if selected_lane == "body_repeal_candidate" else "not_selected",
            )
        )

    rejected_lanes: list[tuple[str, str]] = []
    if preamble_text and selected_lane != "preamble":
        rejected_lanes.append(("preamble", selected_reason))
    if sec1_text and not selected_lane.startswith("sec1_fallback"):
        rejected_lanes.append(
            (
                "sec1_fallback",
                strict_block_reason if pre_routing_sec1_blocked or post_routing_sec1_blocked else (
                    "preamble_selected" if selected_lane == "preamble" else selected_reason
                ),
            )
        )
    if body_lead_text and selected_lane != "body_lead_fallback_pre_routing":
        rejected_lanes.append(
            (
                "body_lead_fallback",
                strict_block_reason if body_lead_pre_routing_blocked else (
                    "preamble_selected" if selected_lane == "preamble" else selected_reason
                ),
            )
        )
    if body_repeal_candidate and selected_lane != "body_repeal_candidate":
        rejected_lanes.append(("body_repeal_candidate", "preamble_selected" if selected_lane == "preamble" else selected_reason))

    return AmendmentAcquisitionResult(
        preamble_text=preamble_text,
        preamble_normalized=preamble_normalized,
        sec1_text=sec1_text,
        sec1_normalized=sec1_normalized,
        body_lead_text=body_lead_text,
        body_lead_normalized=body_lead_normalized,
        body_repeal_candidate=body_repeal_candidate,
        body_repeal_candidate_normalized=body_repeal_candidate_normalized,
        lacks_operative_structure=bool(lacks_operative_structure),
        operative_structure_tags=tuple(operative_structure_tags or ()),
        candidates=tuple(candidates),
        rejected_lanes=tuple(rejected_lanes),
        diagnostics=tuple(diagnostics),
        decision=OperativeLaneDecision(
            selected_lane=selected_lane,
            chosen_operative_text=working_text,
            chosen_normalized_text=working_normalized,
            should_apply=bool(should_apply),
            route_reason=str(route_reason or ""),
            pre_routing_sec1_requested=pre_routing_sec1_requested,
            pre_routing_sec1_applied=pre_routing_sec1_applied,
            post_routing_sec1_applied=post_routing_sec1_applied,
            body_repeal_candidate_used=body_repeal_candidate_used,
            citation_guard_johto=citation_guard_johto,
            citation_guard_sec1=citation_guard_sec1,
            route_target_amendment_id=route_target_amendment_id,
        ),
    )
