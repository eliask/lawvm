from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence

import lxml.etree as etree

from lawvm.core.compile_result import StrictProfile
from lawvm.core.provenance import SourceAnchor, compute_source_anchor
from lawvm.core.source_lane import SourceLaneAttempt, SourceLaneSelectionEvidence
from lawvm.finland.citation_routing import (
    OP_KEYWORDS,
    extract_pending_amendment_target_id,
    route_amendment,
)
from lawvm.finland.metadata import (
    _normalize_johtolause_verbs,
    get_johtolause_from_tree,
    get_operative_body_repeal_candidate_from_tree,
)
from lawvm.finland.scope import fi_statute_citation_spans, restrict_sec1_fallback_to_parent

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
    preamble_body_lead_combine_requested: bool
    preamble_body_lead_combine_applied: bool
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
    # Byte span of the johtolause clause in the RAW amendment source bytes, when
    # it is present verbatim. None (fail-loud) when no contiguous verbatim span
    # exists. The clause selected here is the operative lane's chosen text.
    source_anchor: SourceAnchor | None = None


@dataclass(frozen=True, slots=True)
class Sec1ParentTargetSpanEvidence:
    parent_citation_span: tuple[int, int] | None
    target_token_starts: tuple[int, ...]
    target_char_starts: tuple[int, ...]
    structural_target_count: int
    parser_lane: str
    usable_full_text: bool
    reason: str


def operative_lane_selection_evidence(result: AmendmentAcquisitionResult) -> dict[str, object]:
    """Project operative-text lane selection through the shared source-lane carrier."""

    selected_attempt_lane = next((candidate.lane for candidate in result.candidates if candidate.selected), "")
    selection_detail = {
        "should_apply": result.decision.should_apply,
        "route_target_amendment_id": result.decision.route_target_amendment_id,
        "pre_routing_sec1_requested": result.decision.pre_routing_sec1_requested,
        "pre_routing_sec1_applied": result.decision.pre_routing_sec1_applied,
        "preamble_body_lead_combine_requested": result.decision.preamble_body_lead_combine_requested,
        "preamble_body_lead_combine_applied": result.decision.preamble_body_lead_combine_applied,
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
            lane_attempt_status="selected" if candidate.selected else candidate.reason or "not_selected",
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
    if not johto:
        return True
    if any(keyword in johto.lower() for keyword in OP_KEYWORDS):
        return False
    return len(johto) < 50


def should_use_sec1_fallback_post_routing(johto: str, sec1_text: str) -> bool:
    if any(kw in johto.lower() for kw in OP_KEYWORDS):
        return False
    # lawvm-regex: prefilter has-subprovision routing predicate over loaded sec1 text; picks the fallback lane, mints no op/target
    has_subprov = re.search(
        r"§:?n?\s+(?:\d[\d.]*\s+)?(?:kohta|kohdan|momentti|momentin|johdantokappale)",
        sec1_text.lower(),
    )
    pure_repeal_subprov = (
        has_subprov
        and "kumotaan" in sec1_text.lower()
        # lawvm-regex: prefilter operative-verb absence routing predicate; boolean gate, no op
        and not re.search(r"\b(muutetaan|lisätään|korvataan|otetaan)\b", sec1_text.lower())
    )
    return bool(any(kw in sec1_text.lower() for kw in OP_KEYWORDS) and (not has_subprov or pure_repeal_subprov))


_GENERIC_FI_STATUTE_CITATION_RE = re.compile(r"\(\s*\d+\s*/\s*\d{2,4}\s*\)")
_NUMBERED_LIST_ITEM_RE = re.compile(r"(?m)^\s*\d+\)\s*")


def _sec1_numbered_repeal_list_has_foreign_statute_items(sec1_text: str, parent_id: str) -> bool:
    """Return True when a section-1 fallback list cites this parent and other statutes.

    This is an acquisition ownership guard: in a paragraphized repeal list,
    each numbered item owns its own statute citation. Seeing the parent citation
    before one parsed target does not authorize replaying later sibling items
    against the parent statute.
    """
    if not sec1_text or not parent_id:
        return False
    lowered = sec1_text.lower()
    if "kumotaan" not in lowered:
        return False
    # lawvm-regex: prefilter numbered-list presence guard (paragraphized repeal list?); ownership guard, mints no op/target
    if _NUMBERED_LIST_ITEM_RE.search(sec1_text) is None:
        return False
    parent_spans = set(fi_statute_citation_spans(sec1_text, parent_id))
    if not parent_spans:
        return False
    # lawvm-regex: prefilter generic statute-citation span GUARD; counts foreign-statute citations outside parent spans to refuse cross-statute replay, mints no op
    for match in _GENERIC_FI_STATUTE_CITATION_RE.finditer(sec1_text):
        if (match.start(), match.end()) not in parent_spans:
            return True
    return False


def _sec1_parent_target_span_evidence(sec1_text: str, parent_id: str) -> Sec1ParentTargetSpanEvidence:
    """Decide whether the full sec_1 fallback is parent-owned from parser spans."""
    if not sec1_text or not parent_id:
        return Sec1ParentTargetSpanEvidence(
            parent_citation_span=None,
            target_token_starts=(),
            target_char_starts=(),
            structural_target_count=0,
            parser_lane="",
            usable_full_text=False,
            reason="missing_text_or_parent",
        )

    span_text = " ".join(sec1_text.split())
    parent_citations = fi_statute_citation_spans(span_text, parent_id)
    if not parent_citations:
        return Sec1ParentTargetSpanEvidence(
            parent_citation_span=None,
            target_token_starts=(),
            target_char_starts=(),
            structural_target_count=0,
            parser_lane="",
            usable_full_text=False,
            reason="parent_citation_not_found",
        )
    if _sec1_numbered_repeal_list_has_foreign_statute_items(sec1_text, parent_id):
        return Sec1ParentTargetSpanEvidence(
            parent_citation_span=parent_citations[0],
            target_token_starts=(),
            target_char_starts=(),
            structural_target_count=0,
            parser_lane="",
            usable_full_text=False,
            reason="numbered_repeal_list_contains_foreign_statute_items",
        )

    from lawvm.finland.johtolause.api import parse_clause
    from lawvm.finland.johtolause.lexer import tokenize
    from lawvm.finland.johtolause.scan import apply_annotations_with_jolloin_pairs

    parse_result = parse_clause(span_text, statute_id=parent_id)
    if parse_result.parse_error is not None:
        return Sec1ParentTargetSpanEvidence(
            parent_citation_span=None,
            target_token_starts=(),
            target_char_starts=(),
            structural_target_count=0,
            parser_lane=parse_result.parser_lane,
            usable_full_text=False,
            reason="parse_error",
        )

    parsed_ops = tuple(parse_result.parsed_ops)
    if not parsed_ops:
        return Sec1ParentTargetSpanEvidence(
            parent_citation_span=None,
            target_token_starts=(),
            target_char_starts=(),
            structural_target_count=0,
            parser_lane=parse_result.parser_lane,
            usable_full_text=False,
            reason="no_structural_targets",
        )

    parser_tokens, _jolloin_pairs = apply_annotations_with_jolloin_pairs(tokenize(span_text))
    token_starts: list[int] = []
    char_starts: list[int] = []
    for parsed_op in parsed_ops:
        if parsed_op.source_tokens is None:
            return Sec1ParentTargetSpanEvidence(
                parent_citation_span=None,
                target_token_starts=tuple(token_starts),
                target_char_starts=tuple(char_starts),
                structural_target_count=len(parsed_ops),
                parser_lane=parse_result.parser_lane,
                usable_full_text=False,
                reason="missing_source_tokens",
            )
        token_start = parsed_op.source_tokens[0]
        if token_start < 0 or token_start >= len(parser_tokens):
            return Sec1ParentTargetSpanEvidence(
                parent_citation_span=None,
                target_token_starts=tuple(token_starts),
                target_char_starts=tuple(char_starts),
                structural_target_count=len(parsed_ops),
                parser_lane=parse_result.parser_lane,
                usable_full_text=False,
                reason="source_token_out_of_range",
            )
        char_start = parser_tokens[token_start].char_start
        if char_start < 0:
            return Sec1ParentTargetSpanEvidence(
                parent_citation_span=None,
                target_token_starts=tuple(token_starts),
                target_char_starts=tuple(char_starts),
                structural_target_count=len(parsed_ops),
                parser_lane=parse_result.parser_lane,
                usable_full_text=False,
                reason="source_token_without_char_span",
            )
        token_starts.append(token_start)
        char_starts.append(char_start)

    for citation_span in parent_citations:
        if all(char_start >= citation_span[1] for char_start in char_starts):
            return Sec1ParentTargetSpanEvidence(
                parent_citation_span=citation_span,
                target_token_starts=tuple(token_starts),
                target_char_starts=tuple(char_starts),
                structural_target_count=len(parsed_ops),
                parser_lane=parse_result.parser_lane,
                usable_full_text=True,
                reason="all_parser_targets_after_parent_citation",
            )

    return Sec1ParentTargetSpanEvidence(
        parent_citation_span=parent_citations[-1],
        target_token_starts=tuple(token_starts),
        target_char_starts=tuple(char_starts),
        structural_target_count=len(parsed_ops),
        parser_lane=parse_result.parser_lane,
        usable_full_text=False,
        reason="parser_targets_before_parent_citation",
    )


def _extract_sec1_text(muutos_tree: etree._Element, parent_id: str) -> str:
    sec1_el = muutos_tree.find(".//{*}section[@eId='sec_1']")
    if sec1_el is None:
        return ""
    sec1_text = etree.tostring(sec1_el, method="text", encoding="unicode").strip()
    sec1_text = re.sub(r"^\d+\s*[a-zäöå]?\s*§\s*", "", sec1_text).strip()
    if _sec1_parent_target_span_evidence(sec1_text, parent_id).usable_full_text:
        return sec1_text
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


def _preamble_requests_body_lead_continuation(preamble_text: str, body_lead_text: str) -> bool:
    """Recognize a split operative formula, not a competing body fallback.

    Finnish amendments sometimes put the first operation in the preamble and
    continue the same conjunctive formula in the first unnumbered body section:
    ``kumotaan ... sekä`` + ``muutetaan ... seuraavasti``.  The continuation is
    valid only when both lanes are operative and the preamble visibly ends in
    the open coordinator.
    """
    preamble_stripped = (preamble_text or "").strip()
    body_lead_lower = (body_lead_text or "").lower()
    if not preamble_stripped or not body_lead_lower:
        return False
    if not any(kw in preamble_stripped.lower() for kw in OP_KEYWORDS):
        return False
    if not any(kw in body_lead_lower for kw in OP_KEYWORDS):
        return False
    return preamble_stripped.rstrip(" ,;:").lower().endswith(" sekä")


def build_amendment_acquisition_result(
    *,
    xml_bytes: bytes,
    muutos_tree: etree._Element | None = None,
    parent_id: str,
    amendment_id: str,
    source_title: str,
    parent_title: str,
    parent_issue_date: str = "",
    strict_profile: Optional[StrictProfile] = None,
    lacks_operative_structure: Optional[bool] = None,
    operative_structure_tags: Optional[Sequence[str]] = None,
) -> AmendmentAcquisitionResult:
    if muutos_tree is None:
        muutos_tree = etree.fromstring(xml_bytes)
    if lacks_operative_structure is None or operative_structure_tags is None:
        lacks_operative_structure, operative_structure_tags = amendment_lacks_operative_structure(muutos_tree)

    preamble_text = get_johtolause_from_tree(muutos_tree)
    preamble_normalized = _normalize_johtolause_verbs(preamble_text or "")
    sec1_text = _extract_sec1_text(muutos_tree, parent_id)
    sec1_normalized = _normalize_johtolause_verbs(sec1_text) if sec1_text else ""
    body_lead_text = _extract_body_lead_text(muutos_tree)
    body_lead_normalized = _normalize_johtolause_verbs(body_lead_text) if body_lead_text else ""

    body_repeal_candidate = ""
    if lacks_operative_structure:
        body_repeal_candidate = get_operative_body_repeal_candidate_from_tree(muutos_tree)
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
    preamble_body_lead_combine_requested = bool(
        not pre_routing_sec1_applied
        and body_lead_text
        and _preamble_requests_body_lead_continuation(preamble_text, body_lead_text)
    )
    preamble_body_lead_combine_applied = bool(
        preamble_body_lead_combine_requested
        and allows_context_dependent_anchor_resolution
    )
    preamble_body_lead_combine_blocked = bool(
        preamble_body_lead_combine_requested
        and not allows_context_dependent_anchor_resolution
    )
    preamble_body_lead_combined_text = ""
    preamble_body_lead_combined_normalized = ""
    if preamble_body_lead_combine_requested:
        preamble_body_lead_combined_text = f"{preamble_text.rstrip()} {body_lead_text.lstrip()}"
        preamble_body_lead_combined_normalized = _normalize_johtolause_verbs(
            preamble_body_lead_combined_text
        )

    working_text = preamble_text
    body_repeal_candidate_used = False
    if pre_routing_sec1_applied:
        working_text = sec1_text
    elif preamble_body_lead_combine_applied:
        working_text = preamble_body_lead_combined_text
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
        parent_issue_date=parent_issue_date,
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
    elif preamble_body_lead_combine_applied:
        selected_lane = "preamble_body_lead_combined"
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
        "preamble_body_lead_combined": "preamble_trailing_coordinator_body_lead_continuation",
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
    if preamble_body_lead_combine_blocked:
        diagnostics.append(
            AcquisitionDiagnostic(
                rule_id="ACQ.OPERATIVE_LANE_STRICT_BLOCKED",
                family="target_resolution_recovery",
                phase="acquisition",
                reason="strict profile blocked split preamble/body operative formula composition",
                lane="preamble_body_lead_combined",
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
            reason=(
                selected_reason
                if selected_lane == "preamble"
                else (
                    "composed_into_preamble_body_lead_combined"
                    if selected_lane == "preamble_body_lead_combined"
                    else "not_selected"
                )
            ),
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
                reason=(
                    selected_reason
                    if selected_lane == "body_lead_fallback_pre_routing"
                    else (
                        "composed_into_preamble_body_lead_combined"
                        if selected_lane == "preamble_body_lead_combined"
                        else strict_block_reason if body_lead_pre_routing_blocked else "not_selected"
                    )
                ),
            )
        )
    if preamble_body_lead_combined_text:
        candidates.append(
            OperativeLaneCandidate(
                lane="preamble_body_lead_combined",
                raw_text=preamble_body_lead_combined_text,
                normalized_text=preamble_body_lead_combined_normalized,
                usable=bool(preamble_body_lead_combined_text),
                selected=selected_lane == "preamble_body_lead_combined",
                reason=(
                    selected_reason
                    if selected_lane == "preamble_body_lead_combined"
                    else strict_block_reason if preamble_body_lead_combine_blocked else "not_selected"
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
    if preamble_text and selected_lane not in {"preamble", "preamble_body_lead_combined"}:
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
    if body_lead_text and selected_lane not in {
        "body_lead_fallback_pre_routing",
        "preamble_body_lead_combined",
    }:
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

    # Byte-level source anchoring (certified-transition trace spec §5.1/§7):
    # try to locate the chosen operative clause verbatim in the RAW amendment
    # source bytes. compute_source_anchor returns None (fail-loud, never
    # fabricated) whenever the lane text is not a single contiguous verbatim
    # byte substring — the common case after text-flattening across XML tag
    # boundaries. ``working_text`` is the lane's raw (un-normalized) text.
    source_anchor = compute_source_anchor(
        source_artifact_id=amendment_id,
        raw_bytes=xml_bytes,
        clause_text=working_text or "",
    )

    return AmendmentAcquisitionResult(
        source_anchor=source_anchor,
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
            preamble_body_lead_combine_requested=preamble_body_lead_combine_requested,
            preamble_body_lead_combine_applied=preamble_body_lead_combine_applied,
            post_routing_sec1_applied=post_routing_sec1_applied,
            body_repeal_candidate_used=body_repeal_candidate_used,
            citation_guard_johto=citation_guard_johto,
            citation_guard_sec1=citation_guard_sec1,
            route_target_amendment_id=route_target_amendment_id,
        ),
    )
