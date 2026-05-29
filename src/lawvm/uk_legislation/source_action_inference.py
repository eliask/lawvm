from __future__ import annotations

import re
from lxml import etree as ET
from dataclasses import dataclass
from typing import Any, Optional

from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.lowering_records import (
    _append_uk_effect_lowering_observation,
    _append_uk_effect_lowering_rejection,
)
from lawvm.uk_legislation.metadata_rewrites import _uk_unsupported_metadata_renumber_rejection
from lawvm.uk_legislation.nlp_parser import parse_fragment_substitution
from lawvm.uk_legislation.source_context import _preview_source_text
from lawvm.uk_legislation.source_parent_payloads import (
    UK_SOURCE_PARENT_WHOLE_SCHEDULE_INSERT_RULE_ID,
    _source_parent_at_end_added_payload,
    _source_parent_substitution_range_payload,
    _source_parent_whole_schedule_insert_payload,
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
    extracted_el: Optional[ET._Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> None:
    """Record the terminal missing-action lane after source inference fails."""
    unsupported_renumber = _uk_unsupported_metadata_renumber_rejection(effect)
    if unsupported_renumber is not None:
        _append_uk_effect_lowering_rejection(
            lowering_rejections_out,
            rule_id=unsupported_renumber.rule_id,
            family="lineage_normalization",
            reason_code=unsupported_renumber.reason_code,
            reason=unsupported_renumber.reason,
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "effect_type_normalized": effect_type,
                "source_target": str(unsupported_renumber.source_target),
                "destination": str(unsupported_renumber.destination),
            },
        )
        return
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


def _empty_effect_type_application_modification_table_source(text: str) -> bool:
    """Return true for application/modification tables, not amendment tables."""
    normalized = " ".join((text or "").split()).strip().lower()
    if not normalized:
        return False
    return (
        "nature of provision" in normalized
        and "modifications and limitations" in normalized
        and "enactment" in normalized[:300]
    )


def _source_section_target_matches_affected_provision(
    *,
    text: str,
    affected_provisions: str,
) -> bool:
    source_match = re.search(
        r"\bin\s+section\s+(?P<section>[0-9A-Za-z]+)\s*"
        r"\((?P<subsection>[0-9A-Za-z]+)\)",
        text,
        flags=re.I,
    )
    affected_match = re.fullmatch(
        r"\s*s\.\s*(?P<section>[0-9A-Za-z]+)\s*"
        r"\((?P<subsection>[0-9A-Za-z]+)\)\s*",
        affected_provisions or "",
        flags=re.I,
    )
    if source_match is None or affected_match is None:
        return False
    return (
        source_match.group("section").lower() == affected_match.group("section").lower()
        and source_match.group("subsection").lower()
        == affected_match.group("subsection").lower()
    )


_EMPTY_TYPE_TEXT_INSERTION_FRAGMENT_RULES = frozenset(
    {
        "uk_effect_after_quoted_anchor_insert_text_patch",
        "uk_effect_at_end_text_insertion_patch",
        "uk_effect_compound_lettered_text_patch_instruction",
    }
)


def _empty_effect_type_text_insertions(
    *,
    text: str,
    affected_provisions: str,
) -> tuple[dict[str, Any], ...]:
    normalized = " ".join((text or "").split()).strip()
    if not normalized:
        return ()
    if re.search(r"\b(?:there\s+is\s+inserted|insert)\b", normalized, flags=re.I) is None:
        return ()
    fragments = tuple(dict(fragment) for fragment in parse_fragment_substitution(normalized))
    if not fragments:
        return ()
    for fragment in fragments:
        rule_id = str(fragment.get("rule_id") or "")
        original = str(fragment.get("original") or "")
        replacement = str(fragment.get("replacement") or "")
        if (
            rule_id not in _EMPTY_TYPE_TEXT_INSERTION_FRAGMENT_RULES
            or not original
            or not replacement
            or replacement == original
        ):
            return ()
    has_at_end_fragment = any(
        str(fragment.get("rule_id") or "") == "uk_effect_at_end_text_insertion_patch"
        for fragment in fragments
    )
    if (len(fragments) > 1 or has_at_end_fragment) and not _source_section_target_matches_affected_provision(
        text=normalized,
        affected_provisions=affected_provisions,
    ):
        return ()
    return fragments


def infer_uk_effect_action_from_source(  # noqa: PLR0913
    *,
    effect: UKEffectRecord,
    effect_type: str,
    initial_action: Optional[str],
    extracted_el: Optional[ET._Element],
    extracted_text: Optional[str],
    source_root: Optional[ET._Element],
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

    if _empty_effect_type_application_modification_table_source(extracted_text or ""):
        _append_uk_effect_lowering_rejection(
            lowering_rejections_out,
            rule_id="uk_effect_application_modification_table_rejected",
            family="applicability_scope",
            reason_code="application_modification_table_out_of_scope",
            reason=(
                "UK effect has no explicit text/tree action and the extracted "
                "source is an application/modification table; structural replay "
                "must not infer repeal or replacement from unrelated table cells."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={"effect_type_normalized": effect_type},
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

    text_insertions = _empty_effect_type_text_insertions(
        text=extracted_text or "",
        affected_provisions=effect.affected_provisions,
    )
    if text_insertions:
        compound = len(text_insertions) > 1
        has_at_end = any(
            str(fragment.get("rule_id") or "") == "uk_effect_at_end_text_insertion_patch"
            for fragment in text_insertions
        )
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id=(
                "uk_effect_empty_type_compound_target_local_text_insertions_inferred"
                if compound and has_at_end
                else
                "uk_effect_empty_type_compound_quoted_anchor_word_insertions_inferred"
                if compound
                else "uk_effect_empty_type_quoted_anchor_word_insertion_inferred"
            ),
            family="source_action_inference",
            reason_code=(
                "empty_effect_type_compound_target_local_text_insertions"
                if compound and has_at_end
                else
                "empty_effect_type_compound_quoted_anchor_word_insertions"
                if compound
                else "empty_effect_type_quoted_anchor_word_insertion"
            ),
            reason=(
                "UK effect has no explicit effect type, but the source row "
                "explicitly inserts target-local text fragments; lowering "
                "treats the row as source-owned text rewrite rather than a "
                "structural insertion."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "fragment_count": len(text_insertions),
                "original": str(text_insertions[0].get("original") or ""),
                "replacement": str(
                    text_insertions[0].get("replacement") or ""
                ),
                "fragment_rule_ids": tuple(
                    str(fragment.get("rule_id") or "")
                    for fragment in text_insertions
                ),
            },
        )
        return UKActionInference(action="replace")

    if "repeal" in text_lower or "omit" in text_lower:
        return UKActionInference(action="repeal")
    if "substitute" in text_lower or "replace" in text_lower:
        return UKActionInference(action="replace")
    if "insert" in text_lower:
        return UKActionInference(action="insert")
    if re.search(r"\bfrom\b.*\bto\b", text_lower, re.I | re.S):
        return UKActionInference(action="replace")

    source_parent_whole_schedule_insert = _source_parent_whole_schedule_insert_payload(
        extracted_el=extracted_el,
        source_root=source_root,
        extracted_text=extracted_text,
        target_refs=_split_metadata_provisions(effect.affected_provisions),
    )
    if source_parent_whole_schedule_insert is not None:
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id=UK_SOURCE_PARENT_WHOLE_SCHEDULE_INSERT_RULE_ID,
            family="source_action_inference",
            reason_code="empty_effect_type_source_parent_whole_schedule_insert",
            reason=(
                "UK effect has no explicit effect type, but the source parent "
                "formula explicitly inserts a whole Schedule payload; lowering "
                "infers only the source-witnessed insert action."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail=source_parent_whole_schedule_insert,
        )
        return UKActionInference(action="insert")

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
