"""UK archive source-surface availability classification."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from lxml import etree as ET

from lawvm.core.diagnostic_records import diagnostic_detail
from lawvm.core.source_lane import SourceLaneAttempt, SourceLaneSelectionEvidence

MIN_UK_XML_SOURCE_BYTES = 100
UK_AFFECTING_ACT_XML_SOURCE_RULE_IDS = frozenset(
    {
        "uk_affecting_act_xml_missing_rejected",
        "uk_affecting_act_xml_too_small_rejected",
        "uk_affecting_act_xml_parse_rejected",
        "uk_affecting_act_xml_cached_recorded",
        "uk_affecting_act_current_shell_enacted_source_selected",
        "uk_affecting_act_missing_current_enacted_source_selected",
        "uk_affecting_act_single_amendment_child_source_selected",
        "uk_affecting_act_nonaddressable_schedule_part_context_ignored",
        "uk_affecting_act_single_unnumbered_schedule_context_ignored",
        "uk_affecting_act_implicit_first_subparagraph_context_ignored",
        "uk_affecting_act_parenthesized_range_source_extracted",
        "uk_affecting_act_same_level_parenthetical_source_component_selected",
        "uk_affecting_act_enacted_schedule_table_row_source_extracted",
        "uk_affecting_act_article_schedule_payload_source_extracted",
        "uk_affecting_act_compound_payload_only_block_amendment_selected",
        "uk_affecting_act_block_amendment_payload_descendant_ref_rejected",
        "uk_affecting_act_compound_reference_split_fallback",
        "uk_affecting_act_schedule_part_standalone_split_rejected",
    }
)


class UKSourceStatus(StrEnum):
    ABSENT = "absent"
    TOO_SMALL = "too_small"
    AVAILABLE = "available"


class UKStatuteXmlContentStatus(StrEnum):
    ABSENT = "absent"
    TOO_SMALL = "too_small"
    PARSE_ERROR = "parse_error"
    METADATA_ONLY = "metadata_only"
    AVAILABLE = "available"


@dataclass(frozen=True)
class UKSourceState:
    status: UKSourceStatus
    size: int

    @property
    def available(self) -> bool:
        return self.status is UKSourceStatus.AVAILABLE

    @property
    def missing(self) -> bool:
        return not self.available

    def as_legacy_tuple(self) -> tuple[str, int]:
        return self.status.value, self.size


@dataclass(frozen=True)
class UKStatuteXmlContentState:
    status: UKStatuteXmlContentStatus
    size: int
    number_of_provisions: str
    has_body: bool
    has_schedules: bool
    parse_error: str = ""

    @property
    def usable_as_replay_base(self) -> bool:
        return self.status is UKStatuteXmlContentStatus.AVAILABLE

    def to_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "status": self.status.value,
            "size": self.size,
            "number_of_provisions": self.number_of_provisions,
            "has_body": self.has_body,
            "has_schedules": self.has_schedules,
            "usable_as_replay_base": self.usable_as_replay_base,
        }
        if self.parse_error:
            row["parse_error"] = self.parse_error
        return row


def classify_uk_source_blob(blob: bytes | None) -> UKSourceState:
    if blob is None:
        return UKSourceState(status=UKSourceStatus.ABSENT, size=0)
    size = len(blob)
    if size < MIN_UK_XML_SOURCE_BYTES:
        return UKSourceState(status=UKSourceStatus.TOO_SMALL, size=size)
    return UKSourceState(status=UKSourceStatus.AVAILABLE, size=size)


def uk_source_state_wire_tuple(blob: bytes | None) -> tuple[str, int]:
    """Return the stable CLI/CSV wire shape for UK source availability."""
    return classify_uk_source_blob(blob).as_legacy_tuple()


def classify_uk_source_blob_legacy(blob: bytes | None) -> tuple[str, int]:
    return uk_source_state_wire_tuple(blob)


def classify_uk_statute_xml_content(blob: bytes | None) -> UKStatuteXmlContentState:
    """Classify whether an act-level XML blob contains replayable legal structure.

    Some historical UK ``/enacted/data.xml`` records are metadata envelopes with
    ``NumberOfProvisions="0"`` and no body/schedule payload. Parsing them as an
    empty enacted base is source evidence, not a deterministic replay failure.
    """
    source_state = classify_uk_source_blob(blob)
    if source_state.status is UKSourceStatus.ABSENT:
        return UKStatuteXmlContentState(
            status=UKStatuteXmlContentStatus.ABSENT,
            size=source_state.size,
            number_of_provisions="",
            has_body=False,
            has_schedules=False,
        )
    if source_state.status is UKSourceStatus.TOO_SMALL:
        return UKStatuteXmlContentState(
            status=UKStatuteXmlContentStatus.TOO_SMALL,
            size=source_state.size,
            number_of_provisions="",
            has_body=False,
            has_schedules=False,
        )
    assert blob is not None
    try:
        root = ET.fromstring(blob)
    except ET.ParseError as exc:
        return UKStatuteXmlContentState(
            status=UKStatuteXmlContentStatus.PARSE_ERROR,
            size=source_state.size,
            number_of_provisions="",
            has_body=False,
            has_schedules=False,
            parse_error=str(exc),
        )
    number_of_provisions = str(root.get("NumberOfProvisions") or "")
    has_body = _uk_xml_has_local_name(root, "Body")
    has_schedules = _uk_xml_has_local_name(root, "Schedules") or _uk_xml_has_local_name(
        root,
        "Schedule",
    )
    if number_of_provisions == "0" and not has_body and not has_schedules:
        status = UKStatuteXmlContentStatus.METADATA_ONLY
    else:
        status = UKStatuteXmlContentStatus.AVAILABLE
    return UKStatuteXmlContentState(
        status=status,
        size=source_state.size,
        number_of_provisions=number_of_provisions,
        has_body=has_body,
        has_schedules=has_schedules,
    )


def _uk_xml_has_local_name(root: ET._Element, local_name: str) -> bool:
    for el in root.iter():
        if isinstance(el.tag, str) and ET.QName(el).localname == local_name:
            return True
    return False


def _uk_source_diagnostic(
    *,
    rule_id: str,
    family: str,
    phase: str,
    reason: str,
    blocking: bool,
    **detail: Any,
) -> dict[str, Any]:
    return diagnostic_detail(
        rule_id=rule_id,
        family=family,
        phase=phase,
        reason=reason,
        blocking=blocking,
        detail=detail,
    )


def uk_source_xml_parse_rejection(
    *,
    statute_id: str,
    side: str,
    source_url: str,
    exc: Exception,
) -> dict[str, Any]:
    return _uk_source_diagnostic(
        rule_id=f"uk_{side}_xml_parse_rejected",
        family="source_pathology",
        phase="parse",
        reason="UK source XML was available but could not be parsed.",
        blocking=True,
        statute_id=statute_id,
        side=side,
        source_url=source_url,
        exception_type=type(exc).__name__,
        exception_message=str(exc),
    )


def uk_source_parse_observations_from_ir(ir: Any) -> list[dict[str, Any]]:
    """Return nonblocking source-parse observations carried by parsed UK IR."""
    metadata = getattr(ir, "metadata", {}) or {}
    rows = metadata.get("source_parse_observations", ())
    if not isinstance(rows, (list, tuple)):
        return []
    return [dict(row) for row in rows if isinstance(row, dict)]


def uk_affecting_act_xml_missing_rejection(
    *,
    effect_id: str,
    affecting_act_id: str,
    locator: str,
) -> dict[str, Any]:
    return _uk_source_diagnostic(
        rule_id="uk_affecting_act_xml_missing_rejected",
        family="source_pathology",
        phase="acquisition",
        reason="UK affecting act XML was missing from the archive, so the effect source fragment could not be extracted.",
        blocking=True,
        effect_id=effect_id,
        affecting_act_id=affecting_act_id,
        locator=locator,
    )


def uk_affecting_act_class_unmapped_rejection(
    *,
    effect_id: str,
    affecting_act_id: str,
    locator: str,
    affecting_class: str,
) -> dict[str, Any]:
    return _uk_source_diagnostic(
        rule_id="uk_affecting_act_class_unmapped_rejected",
        family="source_pathology",
        phase="acquisition",
        reason=(
            "UK affecting act class has no document-type slug mapping and the effect "
            "carried no resolvable URI, so the affecting-act id was guessed and did not "
            "resolve. Add a class-to-slug mapping (or a usable AffectingURI) rather than "
            "treating this as a generic missing-XML case."
        ),
        blocking=True,
        effect_id=effect_id,
        affecting_act_id=affecting_act_id,
        locator=locator,
        affecting_class=affecting_class,
    )


def uk_affecting_act_xml_parse_rejection(
    *,
    effect_id: str,
    affecting_act_id: str,
    locator: str,
    exc: Exception,
) -> dict[str, Any]:
    return _uk_source_diagnostic(
        rule_id="uk_affecting_act_xml_parse_rejected",
        family="source_pathology",
        phase="parse",
        reason="UK affecting act XML was available but could not be parsed, so the effect source fragment could not be extracted.",
        blocking=True,
        effect_id=effect_id,
        affecting_act_id=affecting_act_id,
        locator=locator,
        exception_type=type(exc).__name__,
        exception_message=str(exc),
    )


def uk_affecting_act_xml_too_small_rejection(
    *,
    effect_id: str,
    affecting_act_id: str,
    locator: str,
    source_size: int,
) -> dict[str, Any]:
    return _uk_source_diagnostic(
        rule_id="uk_affecting_act_xml_too_small_rejected",
        family="source_pathology",
        phase="acquisition",
        reason="UK affecting act XML was present but too small to trust, so the effect source fragment could not be extracted.",
        blocking=True,
        effect_id=effect_id,
        affecting_act_id=affecting_act_id,
        locator=locator,
        source_size=int(source_size),
    )


def uk_affecting_act_current_shell_enacted_source_selected(
    *,
    effect_id: str,
    affecting_act_id: str,
    affecting_provisions: str,
    current_locator: str,
    enacted_locator: str,
    current_source_size: int,
    enacted_source_size: int,
    current_text_preview: str,
    enacted_text_preview: str,
) -> dict[str, Any]:
    return SourceLaneSelectionEvidence(
        rule_id="uk_affecting_act_current_shell_enacted_source_selected",
        phase="acquisition",
        reason=(
            "UK current affecting-act XML extracted only a non-substantive dot-leader "
            "shell, while the official enacted XML contained substantive text for the "
            "same affecting provision."
        ),
        selected_lane="enacted_xml",
        selected_locator=enacted_locator,
        blocking=False,
        attempts=(
            SourceLaneAttempt(
                lane="current_xml",
                locator=current_locator,
                status="rejected_non_substantive_shell",
                detail={"source_size": int(current_source_size), "text_preview": current_text_preview},
            ),
            SourceLaneAttempt(
                lane="enacted_xml",
                locator=enacted_locator,
                status="selected",
                detail={"source_size": int(enacted_source_size), "text_preview": enacted_text_preview},
            ),
        ),
        detail={
            "effect_id": effect_id,
            "affecting_act_id": affecting_act_id,
            "affecting_provisions": affecting_provisions,
            "current_locator": current_locator,
            "enacted_locator": enacted_locator,
            "current_source_size": int(current_source_size),
            "enacted_source_size": int(enacted_source_size),
            "current_text_preview": current_text_preview,
            "enacted_text_preview": enacted_text_preview,
        },
    ).to_diagnostic_detail()


def uk_affecting_act_missing_current_enacted_source_selected(
    *,
    effect_id: str,
    affecting_act_id: str,
    affecting_provisions: str,
    current_locator: str,
    enacted_locator: str,
    current_source_size: int,
    enacted_source_size: int,
    enacted_text_preview: str,
) -> dict[str, Any]:
    return SourceLaneSelectionEvidence(
        rule_id="uk_affecting_act_missing_current_enacted_source_selected",
        phase="acquisition",
        reason=(
            "UK current affecting-act XML did not expose an extractable same-provision "
            "source node, while the official enacted XML contained substantive text for "
            "that exact affecting provision."
        ),
        selected_lane="enacted_xml",
        selected_locator=enacted_locator,
        blocking=False,
        attempts=(
            SourceLaneAttempt(
                lane="current_xml",
                locator=current_locator,
                status="missing_same_provision_source",
                detail={"source_size": int(current_source_size)},
            ),
            SourceLaneAttempt(
                lane="enacted_xml",
                locator=enacted_locator,
                status="selected",
                detail={"source_size": int(enacted_source_size), "text_preview": enacted_text_preview},
            ),
        ),
        detail={
            "effect_id": effect_id,
            "affecting_act_id": affecting_act_id,
            "affecting_provisions": affecting_provisions,
            "current_locator": current_locator,
            "enacted_locator": enacted_locator,
            "current_source_size": int(current_source_size),
            "enacted_source_size": int(enacted_source_size),
            "enacted_text_preview": enacted_text_preview,
        },
    ).to_diagnostic_detail()


def uk_affecting_act_single_amendment_child_source_selected(
    *,
    effect_id: str,
    affecting_act_id: str,
    affecting_provisions: str,
    locator: str,
    authority_layer: str,
    source_container_id: str,
    selected_child_id: str,
    selected_child_label: str,
    selected_child_text_preview: str,
) -> dict[str, Any]:
    reason = (
        "UK effects metadata named a broad source container whose current "
        "version was only a shell, while the enacted source container had "
        "exactly one child carrying an amendment payload. LawVM selected "
        "that child rather than smuggling the context sibling into the payload."
    )
    return SourceLaneSelectionEvidence(
        rule_id="uk_affecting_act_single_amendment_child_source_selected",
        phase="extraction",
        reason=reason,
        selected_lane="single_amendment_child_payload",
        selected_locator=f"{locator}#{selected_child_id}",
        blocking=False,
        attempts=(
            SourceLaneAttempt(
                lane="source_container_context",
                locator=f"{locator}#{source_container_id}",
                status="context_selected_not_payload",
            ),
            SourceLaneAttempt(
                lane="single_amendment_child_payload",
                locator=f"{locator}#{selected_child_id}",
                status="selected",
                detail={
                    "selected_child_label": selected_child_label,
                    "selected_child_text_preview": selected_child_text_preview,
                },
            ),
        ),
        detail={
            "effect_id": effect_id,
            "affecting_act_id": affecting_act_id,
            "affecting_provisions": affecting_provisions,
            "locator": locator,
            "authority_layer": authority_layer,
            "source_container_id": source_container_id,
            "selected_child_id": selected_child_id,
            "selected_child_label": selected_child_label,
            "selected_child_text_preview": selected_child_text_preview,
        },
    ).to_diagnostic_detail()


def uk_affecting_act_nonaddressable_schedule_part_context_ignored(
    *,
    effect_id: str,
    affecting_act_id: str,
    affecting_provisions: str,
    locator: str,
    authority_layer: str,
    requested_part_label: str,
    normalized_affecting_provisions: str,
    extracted_element_id: str,
) -> dict[str, Any]:
    return _uk_source_diagnostic(
        rule_id="uk_affecting_act_nonaddressable_schedule_part_context_ignored",
        family="target_resolution_recovery",
        phase="extraction",
        reason=(
            "UK effects metadata named a schedule Part context that is represented as an "
            "ancestor container in source XML rather than in descendant paragraph IDs; "
            "the normalized paragraph reference was accepted only because the extracted "
            "node has a matching Part ancestor."
        ),
        blocking=False,
        effect_id=effect_id,
        affecting_act_id=affecting_act_id,
        affecting_provisions=affecting_provisions,
        normalized_affecting_provisions=normalized_affecting_provisions,
        locator=locator,
        authority_layer=authority_layer,
        requested_part_label=requested_part_label,
        extracted_element_id=extracted_element_id,
    )


def uk_affecting_act_single_unnumbered_schedule_context_ignored(
    *,
    effect_id: str,
    affecting_act_id: str,
    affecting_provisions: str,
    locator: str,
    authority_layer: str,
    requested_schedule_label: str,
    normalized_affecting_provisions: str,
    schedule_element_id: str,
    source_instruction_id: str,
    extracted_element_id: str,
) -> dict[str, Any]:
    return _uk_source_diagnostic(
        rule_id="uk_affecting_act_single_unnumbered_schedule_context_ignored",
        family="target_resolution_recovery",
        phase="extraction",
        reason=(
            "UK effects metadata named Schedule 1, but the affecting XML exposes a "
            "single unnumbered Schedule; LawVM accepted the paragraph reference only "
            "after proving there is exactly one unnumbered first-Schedule context."
        ),
        blocking=False,
        effect_id=effect_id,
        affecting_act_id=affecting_act_id,
        affecting_provisions=affecting_provisions,
        locator=locator,
        authority_layer=authority_layer,
        requested_schedule_label=requested_schedule_label,
        normalized_affecting_provisions=normalized_affecting_provisions,
        schedule_element_id=schedule_element_id,
        source_instruction_id=source_instruction_id,
        extracted_element_id=extracted_element_id,
    )


def uk_affecting_act_article_schedule_payload_source_extracted(
    *,
    effect_id: str,
    affecting_act_id: str,
    affecting_provisions: str,
    locator: str,
    authority_layer: str,
    article_ref: str,
    article_element_id: str,
    schedule_element_id: str,
    article_text_preview: str,
) -> dict[str, Any]:
    reason = (
        "UK effects metadata cited an article plus an attached Schedule payload; "
        "the article text explicitly points to text set out in the Schedule, so "
        "the unnumbered source Schedule is used as the amendment payload."
    )
    return SourceLaneSelectionEvidence(
        rule_id="uk_affecting_act_article_schedule_payload_source_extracted",
        phase="extraction",
        reason=reason,
        selected_lane="attached_schedule_payload",
        selected_locator=f"{locator}#{schedule_element_id}",
        blocking=False,
        attempts=(
            SourceLaneAttempt(
                lane="article_source_context",
                locator=f"{locator}#{article_element_id}",
                status="context_selected_not_payload",
                detail={"article_ref": article_ref, "article_text_preview": article_text_preview},
            ),
            SourceLaneAttempt(
                lane="attached_schedule_payload",
                locator=f"{locator}#{schedule_element_id}",
                status="selected",
                detail={"schedule_element_id": schedule_element_id},
            ),
        ),
        detail={
            "effect_id": effect_id,
            "affecting_act_id": affecting_act_id,
            "affecting_provisions": affecting_provisions,
            "locator": locator,
            "authority_layer": authority_layer,
            "article_ref": article_ref,
            "article_element_id": article_element_id,
            "schedule_element_id": schedule_element_id,
            "article_text_preview": article_text_preview,
        },
    ).to_diagnostic_detail()


def uk_affecting_act_implicit_first_subparagraph_context_ignored(
    *,
    effect_id: str,
    affecting_act_id: str,
    affecting_provisions: str,
    locator: str,
    authority_layer: str,
    normalized_affecting_provisions: str,
    extracted_element_id: str,
) -> dict[str, Any]:
    return _uk_source_diagnostic(
        rule_id="uk_affecting_act_implicit_first_subparagraph_context_ignored",
        family="target_resolution_recovery",
        phase="extraction",
        reason=(
            "UK effects metadata included an inserted first subparagraph context in a "
            "schedule paragraph source reference, but the affecting XML exposes the "
            "lettered child directly under the source paragraph; LawVM accepted the "
            "normalized reference only after the exact source reference missed."
        ),
        blocking=False,
        effect_id=effect_id,
        affecting_act_id=affecting_act_id,
        affecting_provisions=affecting_provisions,
        normalized_affecting_provisions=normalized_affecting_provisions,
        locator=locator,
        authority_layer=authority_layer,
        extracted_element_id=extracted_element_id,
    )


def uk_affecting_act_parenthesized_range_source_extracted(
    *,
    effect_id: str,
    affecting_act_id: str,
    affecting_provisions: str,
    locator: str,
    authority_layer: str,
    normalized_parent_ref: str,
    requested_start_label: str,
    requested_end_label: str,
    extracted_element_ids: list[str],
) -> dict[str, Any]:
    return _uk_source_diagnostic(
        rule_id="uk_affecting_act_parenthesized_range_source_extracted",
        family="source_range_extraction",
        phase="extraction",
        reason=(
            "UK effects metadata named a parenthesized source range whose individual "
            "children are addressable in the affecting XML; LawVM extracted only the "
            "bounded child range into a synthetic source wrapper instead of widening "
            "to the whole parent provision."
        ),
        blocking=False,
        effect_id=effect_id,
        affecting_act_id=affecting_act_id,
        affecting_provisions=affecting_provisions,
        normalized_parent_ref=normalized_parent_ref,
        locator=locator,
        authority_layer=authority_layer,
        requested_start_label=requested_start_label,
        requested_end_label=requested_end_label,
        extracted_element_ids=extracted_element_ids,
    )


def uk_affecting_act_enacted_schedule_table_row_source_extracted(
    *,
    effect_id: str,
    affecting_act_id: str,
    affected_provisions: str,
    affecting_provisions: str,
    locator: str,
    authority_layer: str,
    schedule_label: str,
    part_label: str,
    target_label: str,
    source_row_text: str,
) -> dict[str, Any]:
    reason = (
        "UK current affecting-act XML was unavailable, while the official enacted "
        "source exposed a unique table row under the affected schedule Part whose "
        "first cell exactly names the added schedule paragraph; LawVM extracted "
        "only that row as a synthetic paragraph payload instead of admitting the "
        "whole schedule source."
    )
    selected_locator = f"{locator}#schedule-{schedule_label}-part-{part_label}-row-{target_label}"
    return SourceLaneSelectionEvidence(
        rule_id="uk_affecting_act_enacted_schedule_table_row_source_extracted",
        phase="extraction",
        reason=reason,
        selected_lane="enacted_schedule_table_row_payload",
        selected_locator=selected_locator,
        blocking=False,
        attempts=(
            SourceLaneAttempt(
                lane="enacted_schedule_table_row_payload",
                locator=selected_locator,
                status="selected",
                detail={
                    "schedule_label": schedule_label,
                    "part_label": part_label,
                    "target_label": target_label,
                    "source_row_text": source_row_text,
                },
            ),
        ),
        detail={
            "effect_id": effect_id,
            "affecting_act_id": affecting_act_id,
            "affected_provisions": affected_provisions,
            "affecting_provisions": affecting_provisions,
            "locator": locator,
            "authority_layer": authority_layer,
            "schedule_label": schedule_label,
            "part_label": part_label,
            "target_label": target_label,
            "source_row_text": source_row_text,
        },
    ).to_diagnostic_detail()


def uk_affecting_act_compound_payload_only_block_amendment_selected(
    *,
    effect_id: str,
    affecting_act_id: str,
    affecting_provisions: str,
    locator: str,
    authority_layer: str,
    source_row_tag: str,
    source_row_id: str,
    source_row_label: str,
    payload_container_tag: str,
    payload_text_preview: str,
) -> dict[str, Any]:
    reason = (
        "UK compound affecting-source metadata selected a numbered source row whose "
        "only substantive content is a BlockAmendment/InlineAmendment payload. "
        "LawVM uses the amendment payload container rather than smuggling the "
        "source row label into payload text."
    )
    return SourceLaneSelectionEvidence(
        rule_id="uk_affecting_act_compound_payload_only_block_amendment_selected",
        phase="extraction",
        reason=reason,
        selected_lane="block_amendment_payload_container",
        selected_locator=f"{locator}#{source_row_id}/payload",
        blocking=False,
        attempts=(
            SourceLaneAttempt(
                lane="numbered_source_row_context",
                locator=f"{locator}#{source_row_id}",
                status="context_selected_not_payload",
                detail={"source_row_tag": source_row_tag, "source_row_label": source_row_label},
            ),
            SourceLaneAttempt(
                lane="block_amendment_payload_container",
                locator=f"{locator}#{source_row_id}/payload",
                status="selected",
                detail={
                    "payload_container_tag": payload_container_tag,
                    "payload_text_preview": payload_text_preview,
                },
            ),
        ),
        detail={
            "effect_id": effect_id,
            "affecting_act_id": affecting_act_id,
            "affecting_provisions": affecting_provisions,
            "locator": locator,
            "authority_layer": authority_layer,
            "source_row_tag": source_row_tag,
            "source_row_id": source_row_id,
            "source_row_label": source_row_label,
            "payload_container_tag": payload_container_tag,
            "payload_text_preview": payload_text_preview,
        },
    ).to_diagnostic_detail()


def uk_affecting_act_block_amendment_payload_descendant_ref_rejection(
    *,
    effect_id: str,
    affecting_act_id: str,
    affecting_provisions: str,
    locator: str,
    authority_layer: str,
    extracted_tag: str,
    extracted_label: str,
    extracted_text_preview: str,
    amendment_container_tag: str,
    source_instruction_ancestor_tag: str,
    source_instruction_ancestor_id: str,
    source_instruction_ancestor_label: str,
    source_instruction_ancestor_text_preview: str,
) -> dict[str, Any]:
    return _uk_source_diagnostic(
        rule_id="uk_affecting_act_block_amendment_payload_descendant_ref_rejected",
        family="source_pathology",
        phase="extraction",
        reason=(
            "UK effects metadata named an affecting source provision, but greedy "
            "source extraction resolved the reference to an anonymous descendant "
            "inside a BlockAmendment/InlineAmendment payload. That payload child is "
            "amended text, not the cited source instruction, so LawVM rejects it "
            "instead of treating it as source context."
        ),
        blocking=True,
        effect_id=effect_id,
        affecting_act_id=affecting_act_id,
        affecting_provisions=affecting_provisions,
        locator=locator,
        authority_layer=authority_layer,
        extracted_tag=extracted_tag,
        extracted_label=extracted_label,
        extracted_text_preview=extracted_text_preview,
        amendment_container_tag=amendment_container_tag,
        source_instruction_ancestor_tag=source_instruction_ancestor_tag,
        source_instruction_ancestor_id=source_instruction_ancestor_id,
        source_instruction_ancestor_label=source_instruction_ancestor_label,
        source_instruction_ancestor_text_preview=source_instruction_ancestor_text_preview,
    )


def uk_affecting_act_outdented_child_source_selected(
    *,
    effect_id: str,
    affecting_act_id: str,
    affecting_provisions: str,
    locator: str,
    authority_layer: str,
    requested_parent_id: str,
    selected_child_id: str,
    selected_child_label: str,
    selected_child_text_preview: str,
    carried_parent_label: str,
) -> dict[str, Any]:
    return _uk_source_diagnostic(
        rule_id="uk_affecting_act_outdented_child_source_selected",
        family="source_context_recovery",
        phase="extraction",
        reason=(
            "UK source XML outdented a lettered source child from the numbered "
            "provision named by the effects metadata. LawVM selected the "
            "outdented child only because it shares the same source parent and "
            "its own instruction explicitly names the carried subsection."
        ),
        blocking=False,
        effect_id=effect_id,
        affecting_act_id=affecting_act_id,
        affecting_provisions=affecting_provisions,
        locator=locator,
        authority_layer=authority_layer,
        requested_parent_id=requested_parent_id,
        selected_child_id=selected_child_id,
        selected_child_label=selected_child_label,
        selected_child_text_preview=selected_child_text_preview,
        carried_parent_label=carried_parent_label,
    )


def uk_affecting_act_compound_reference_split_fallback(
    *,
    effect_id: str,
    affecting_act_id: str,
    affecting_provisions: str,
    locator: str,
    authority_layer: str,
    split_first_part: str,
    split_second_part: str,
    split_selected_part: str,
    extracted_element_id: str,
) -> dict[str, Any]:
    return _uk_source_diagnostic(
        rule_id="uk_affecting_act_compound_reference_split_fallback",
        family="target_resolution_recovery",
        phase="extraction",
        reason=(
            "UK affecting provisions contained a compound/combined reference that either "
            "failed to extract as one address or initially resolved only to a gateway "
            "provision. LawVM split the reference at an explicit structural component "
            "boundary and extracted one component without treating the gateway and "
            "payload references as a single address."
        ),
        blocking=False,
        effect_id=effect_id,
        affecting_act_id=affecting_act_id,
        affecting_provisions=affecting_provisions,
        locator=locator,
        authority_layer=authority_layer,
        split_first_part=split_first_part,
        split_second_part=split_second_part,
        split_selected_part=split_selected_part,
        extracted_element_id=extracted_element_id,
    )


def uk_affecting_act_same_level_parenthetical_source_component_selected(
    *,
    effect_id: str,
    affecting_act_id: str,
    affecting_provisions: str,
    locator: str,
    authority_layer: str,
    selected_ref: str,
    companion_ref: str,
    greedy_extracted_element_id: str,
    selected_element_id: str,
    companion_element_id: str,
    selected_text_preview: str,
    companion_text_preview: str,
) -> dict[str, Any]:
    return _uk_source_diagnostic(
        rule_id="uk_affecting_act_same_level_parenthetical_source_component_selected",
        family="source_context_recovery",
        phase="extraction",
        reason=(
            "UK effects metadata used a compact same-level source citation such as "
            "s. N(a)(b). Greedy extraction resolved that surface as a nested path, "
            "but the source XML exposes the parenthesized numbers as sibling "
            "provisions. LawVM selected the operative first sibling and recorded "
            "the companion sibling instead of passing an unrelated nested child to "
            "lowering."
        ),
        blocking=False,
        effect_id=effect_id,
        affecting_act_id=affecting_act_id,
        affecting_provisions=affecting_provisions,
        locator=locator,
        authority_layer=authority_layer,
        selected_ref=selected_ref,
        companion_ref=companion_ref,
        greedy_extracted_element_id=greedy_extracted_element_id,
        selected_element_id=selected_element_id,
        companion_element_id=companion_element_id,
        selected_text_preview=selected_text_preview,
        companion_text_preview=companion_text_preview,
    )


def uk_affecting_act_schedule_part_standalone_split_rejection(
    *,
    effect_id: str,
    affecting_act_id: str,
    affecting_provisions: str,
    locator: str,
    authority_layer: str,
    split_first_part: str,
    split_second_part: str,
    schedule_component_tag: str,
    schedule_component_id: str,
    schedule_component_label: str,
    standalone_part_candidate_tag: str,
    standalone_part_candidate_id: str,
    standalone_part_candidate_label: str,
) -> dict[str, Any]:
    return _uk_source_diagnostic(
        rule_id="uk_affecting_act_schedule_part_standalone_split_rejected",
        family="source_pathology",
        phase="extraction",
        reason=(
            "UK affecting provisions named a schedule part, but source extraction could "
            "not resolve that part while preserving the schedule container. LawVM rejects "
            "the attempted standalone Part/Pt split because it may select a main-body "
            "Part with the same label and contaminate the amendment payload."
        ),
        blocking=True,
        effect_id=effect_id,
        affecting_act_id=affecting_act_id,
        affecting_provisions=affecting_provisions,
        locator=locator,
        authority_layer=authority_layer,
        split_first_part=split_first_part,
        split_second_part=split_second_part,
        schedule_component_tag=schedule_component_tag,
        schedule_component_id=schedule_component_id,
        schedule_component_label=schedule_component_label,
        standalone_part_candidate_tag=standalone_part_candidate_tag,
        standalone_part_candidate_id=standalone_part_candidate_id,
        standalone_part_candidate_label=standalone_part_candidate_label,
    )


def is_uk_affecting_act_xml_source_observation(row: dict[str, Any]) -> bool:
    return str(row.get("rule_id") or "") in UK_AFFECTING_ACT_XML_SOURCE_RULE_IDS


def is_uk_affecting_act_xml_source_diagnostic(row: dict[str, Any]) -> bool:
    return is_uk_affecting_act_xml_source_observation(row)
