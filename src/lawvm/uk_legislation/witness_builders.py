"""Builders for UK lowering witnesses and temporal replay metadata."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, Optional, Sequence

from lawvm.core.ir import LegalOperation, TextPatchSpec
from lawvm.core.semantic_types import TextPatchKindEnum
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.witnesses import (
    UKApplicabilityWitness,
    UKEffectWitness,
    UKInsertionAnchorWitness,
    UKProvisionExtractionWitness,
    UKTargetExpansionWitness,
    UKTextRewriteSpec,
)
from lawvm.uk_legislation.xml_helpers import _tag

if TYPE_CHECKING:
    from lawvm.core.compile_result import TemporalEvent


def _uk_temporal_group_id(effect: UKEffectRecord) -> str:
    """Return the stable temporal group key for one UK effect."""
    return effect.effect_id


def _uk_temporal_events_from_ops(
    ops: Sequence[LegalOperation],
    *,
    target_statute: str,
) -> tuple[TemporalEvent, ...]:
    """Project replay ops into explicit temporal authority for timeline mode.

    The UK replay path still reads source dates when no temporal events are
    present, but timeline mode should already carry explicit executable
    temporal authority so the core bridge can eventually be retired without
    changing the matcher again.
    """
    from lawvm.core.compile_result import ActivationRule, TemporalEvent, TemporalScope  # noqa: PLC0415
    from lawvm.core.temporal import FIXED_DATE_KIND  # noqa: PLC0415

    events: list[TemporalEvent] = []
    seen_group_ids: set[str] = set()
    for op in ops:
        group_id = str(getattr(op, "group_id", "") or "")
        if not group_id or group_id in seen_group_ids:
            continue
        seen_group_ids.add(group_id)
        source = getattr(op, "source", None)
        if source is None:
            continue
        effective_from = str(getattr(source, "effective", "") or getattr(source, "enacted", "") or "")
        if not effective_from:
            continue
        events.append(
            TemporalEvent(
                event_id=f"uk-temporal:{group_id}",
                group_id=group_id,
                kind="commence",
                scope=TemporalScope(target_statute=target_statute),
                effective=effective_from,
                source=source,
                activation_rule=ActivationRule(
                    kind=FIXED_DATE_KIND,
                    effective_date=effective_from,
                    raw_text=str(getattr(source, "raw_text", "") or ""),
                ),
            )
        )
    return tuple(events)


def _uk_applicability_witness(effect: UKEffectRecord) -> UKApplicabilityWitness:
    return UKApplicabilityWitness(
        effective_date=effect.effective_date,
        in_force_dates=tuple(
            str(item.get("date") or "") for item in (effect.in_force_dates or []) if str(item.get("date") or "")
        ),
        requires_applied=bool(effect.requires_applied),
        applied=bool(effect.applied),
        effect_type_raw=effect.effect_type,
    )


def _uk_effect_witness(effect: UKEffectRecord, *, authority_layer: str) -> UKEffectWitness:
    return UKEffectWitness(
        effect_id=effect.effect_id,
        affected_provisions_raw=effect.affected_provisions,
        affecting_provisions_raw=effect.affecting_provisions,
        effect_type_raw=effect.effect_type,
        comments_raw=effect.comments,
        authority_layer=authority_layer,
        applicability=_uk_applicability_witness(effect),
    )


def _uk_extraction_witness(
    effect: UKEffectRecord,
    *,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    metadata_fallback_used: bool,
    source_authority_layer: str = "",
) -> UKProvisionExtractionWitness:
    extracted_source_present = extracted_el is not None
    if extracted_source_present:
        authority_layer = source_authority_layer or "AFFECTING_ACT_TEXT"
        extraction_failure_kind = None
    elif metadata_fallback_used:
        authority_layer = "CURRENT_XML_METADATA_BACKFILL"
        extraction_failure_kind = "missing_extracted_source"
    else:
        authority_layer = "EFFECT_FEED_INDEX"
        extraction_failure_kind = "missing_extracted_source"
    return UKProvisionExtractionWitness(
        effect_id=effect.effect_id,
        authority_layer=authority_layer,
        extracted_tag=_tag(extracted_el) if extracted_el is not None else None,
        extracted_text=extracted_text or "",
        extracted_source_present=extracted_source_present,
        metadata_fallback_used=metadata_fallback_used,
        extraction_failure_kind=extraction_failure_kind,
    )


def _uk_target_expansion_witness(
    original_ref: str,
    expanded_refs: list[str] | tuple[str, ...],
    *,
    original_targets_str: list[str] | tuple[str, ...] | None = None,
) -> UKTargetExpansionWitness:
    expanded_refs_list = list(expanded_refs)
    original_targets_list = list(original_targets_str) if original_targets_str is not None else expanded_refs_list
    if expanded_refs_list == [original_ref]:
        expansion_source = "none"
    elif expanded_refs_list == original_targets_list:
        expansion_source = "metadata_split"
    else:
        expansion_source = "extracted_or_text_expansion"
    return UKTargetExpansionWitness(
        original_ref=original_ref,
        expanded_refs=tuple(expanded_refs_list),
        expansion_source=expansion_source,
    )


def _uk_text_rewrite_spec(
    *,
    fragment_subs: Optional[list],
    text_patch: Optional[TextPatchSpec],
    op_text_match: Optional[str],
    op_text_replacement: Optional[str],
    op_text_occurrence: int,
    op_text_end_occurrence: int = 0,
) -> Optional[UKTextRewriteSpec]:
    if fragment_subs:
        primary = fragment_subs[0]
        occurrence = int(str(primary.get("occurrence") or op_text_occurrence or "0"))
        end_occurrence = int(str(primary.get("end_occurrence") or op_text_end_occurrence or "0"))
        alternatives = tuple(
            (str(item.get("original") or ""), str(item.get("replacement") or ""))
            for item in fragment_subs
            if str(item.get("original") or "")
        )
        return UKTextRewriteSpec(
            primary_match=str(primary.get("original") or "") or None,
            primary_replacement=str(primary.get("replacement") or ""),
            alternatives=alternatives,
            occurrence=occurrence,
            rewrite_source=str(primary.get("rule_id") or "fragment_substitution"),
            end_occurrence=end_occurrence,
        )
    if text_patch is not None:
        primary_match = text_patch.selector.match_text
        primary_replacement = text_patch.replacement or ""
        if text_patch.kind is TextPatchKindEnum.DELETE:
            primary_replacement = ""
        return UKTextRewriteSpec(
            primary_match=primary_match,
            primary_replacement=primary_replacement,
            alternatives=((primary_match, primary_replacement),),
            occurrence=text_patch.selector.occurrence,
            rewrite_source="typed_text_patch",
            end_occurrence=text_patch.selector.end_occurrence,
        )
    if op_text_match is not None:
        return UKTextRewriteSpec(
            primary_match=op_text_match,
            primary_replacement=op_text_replacement,
            alternatives=((op_text_match, op_text_replacement or ""),),
            occurrence=op_text_occurrence,
            rewrite_source="regex_omission_fallback",
            end_occurrence=op_text_end_occurrence,
        )
    return None


def _uk_insertion_anchor_witness(
    preceding_eid: Optional[str],
    *,
    following_eid: Optional[str] = None,
    anchor_source: str = "effect_comments_after_clause",
) -> Optional[UKInsertionAnchorWitness]:
    if not preceding_eid and not following_eid:
        return None
    return UKInsertionAnchorWitness(
        preceding_eid=preceding_eid,
        following_eid=following_eid,
        anchor_source=anchor_source,
    )
