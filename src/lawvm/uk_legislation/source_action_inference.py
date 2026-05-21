from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Optional

from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.lowering_records import _append_uk_effect_lowering_rejection
from lawvm.uk_legislation.source_context import _preview_source_text
from lawvm.uk_legislation.source_parent_payloads import (
    _source_parent_at_end_added_payload,
    _source_parent_substitution_range_payload,
)
from lawvm.uk_legislation.source_text_reclassifications import (
    _empty_effect_type_as_if_words_omitted,
    _empty_effect_type_commencement_source,
    _source_parent_application_modification_context,
)
from lawvm.uk_legislation.target_parser import _split_metadata_provisions


@dataclass(frozen=True)
class UKActionInference:
    action: Optional[str]
    blocked: bool = False
    source_parent_substitution_range_payload: Optional[dict[str, Any]] = None
    source_parent_at_end_added_payload: Optional[dict[str, Any]] = None


def append_no_supported_action_rejection(
    *,
    effect: UKEffectRecord,
    effect_type: str,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> None:
    """Record the terminal missing-action lane after source inference fails."""
    _append_uk_effect_lowering_rejection(
        lowering_rejections_out,
        rule_id="uk_effect_lowering_no_supported_action_rejected",
        family="unsupported_or_unresolved_action",
        reason_code="no_supported_action",
        reason=(
            "UK effect lowered to no replay operations because no supported "
            "action could be inferred"
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={"effect_type_normalized": effect_type},
    )


def infer_uk_effect_action_from_source(  # noqa: PLR0913
    *,
    effect: UKEffectRecord,
    effect_type: str,
    initial_action: Optional[str],
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    source_root: Optional[ET.Element],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> UKActionInference:
    """Infer a missing UK effect action from source-local evidence.

    This owns only the missing-action inference phase. It may emit blocking
    diagnostics and mark the row blocked, but it does not build replay ops.
    """
    if initial_action or extracted_el is None:
        return UKActionInference(action=initial_action)

    text_lower = (extracted_text or "").lower()
    if _empty_effect_type_commencement_source(extracted_text or ""):
        _append_uk_effect_lowering_rejection(
            lowering_rejections_out,
            rule_id="uk_effect_commencement_source_rejected",
            family="applicability_scope",
            reason_code="commencement_source_out_of_scope",
            reason=(
                "UK effect has no explicit text/tree action and the source "
                "is a commencement instrument; structural replay must not "
                "synthesize a mutation from in-force language."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={"effect_type_normalized": effect_type},
        )
        return UKActionInference(action=None, blocked=True)

    application_modification_context = _source_parent_application_modification_context(
        extracted_el=extracted_el,
        source_root=source_root,
    )
    if application_modification_context:
        _append_uk_effect_lowering_rejection(
            lowering_rejections_out,
            rule_id="uk_effect_application_modification_payload_rejected",
            family="applicability_scope",
            reason_code="application_modification_payload_out_of_scope",
            reason=(
                "UK effect has no explicit effect type and the extracted "
                "BlockAmendment payload is governed by a parent "
                "application-modification formula; structural replay must "
                "not treat it as an unconditional current-text amendment."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "parent_context_preview": _preview_source_text(
                    application_modification_context,
                    limit=240,
                ),
            },
        )
        return UKActionInference(action=None, blocked=True)

    if _empty_effect_type_as_if_words_omitted(extracted_text or ""):
        _append_uk_effect_lowering_rejection(
            lowering_rejections_out,
            rule_id="uk_effect_empty_type_as_if_words_omitted_rejected",
            family="temporal_recovery",
            reason_code="empty_effect_type_temporary_as_if_word_omission",
            reason=(
                "UK effect has no explicit effect type and the source uses "
                "temporary 'shall have effect as if words were omitted' "
                "language; lowering must not infer a structural repeal of "
                "the broad affected provision."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={"affected_provisions": effect.affected_provisions},
        )
        return UKActionInference(action=None, blocked=True)

    if "repeal" in text_lower or "omit" in text_lower:
        return UKActionInference(action="repeal")
    if "substitute" in text_lower or "replace" in text_lower:
        return UKActionInference(action="replace")
    if "insert" in text_lower:
        return UKActionInference(action="insert")
    if re.search(r"\bfrom\b.*\bto\b", text_lower, re.I | re.S):
        return UKActionInference(action="replace")

    source_parent_substitution_range_payload = _source_parent_substitution_range_payload(
        extracted_el=extracted_el,
        source_root=source_root,
        extracted_text=extracted_text,
        target_refs=_split_metadata_provisions(effect.affected_provisions),
    )
    if source_parent_substitution_range_payload is not None:
        return UKActionInference(
            action="replace",
            source_parent_substitution_range_payload=source_parent_substitution_range_payload,
        )

    source_parent_at_end_added_payload = _source_parent_at_end_added_payload(
        extracted_el=extracted_el,
        source_root=source_root,
        extracted_text=extracted_text,
        target_refs=_split_metadata_provisions(effect.affected_provisions),
    )
    if source_parent_at_end_added_payload is not None:
        return UKActionInference(
            action="insert",
            source_parent_at_end_added_payload=source_parent_at_end_added_payload,
        )

    return UKActionInference(action=None)
