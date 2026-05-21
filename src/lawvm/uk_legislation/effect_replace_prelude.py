"""Replace-specific target planning for UK effect lowering."""

from __future__ import annotations

from dataclasses import dataclass
import xml.etree.ElementTree as ET
from typing import Any, Optional

from lawvm.uk_legislation.addressing import _addr_leaf_kind, _addr_leaf_label
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.lowering_records import _append_uk_effect_lowering_observation
from lawvm.uk_legislation.source_parent_payloads import (
    UK_SOURCE_PARENT_SUBSTITUTION_RANGE_PAYLOAD_RULE_ID as _UK_SOURCE_PARENT_SUBSTITUTION_RANGE_PAYLOAD_RULE_ID,
)
from lawvm.uk_legislation.substitution_metadata import (
    UK_SOURCE_LABEL_CHANGING_SUBSTITUTION_RULE_ID as _UK_SOURCE_LABEL_CHANGING_SUBSTITUTION_RULE_ID,
    UKSourceLabelChangingSubstitution,
    _source_label_changing_substitution_series,
    _repeal_tail_for_substituted_series_replacement,
)
from lawvm.uk_legislation.target_parser import _parse_affected_target


@dataclass(frozen=True)
class UKReplacePrelude:
    targets_str: list[str]
    trailing_repeal_refs: list[str]
    replacement_leaf_override: Optional[str]
    replacement_leaf_kind: Optional[str]
    label_changing_substitutions: tuple[UKSourceLabelChangingSubstitution, ...]


def plan_replace_effect_prelude(
    *,
    effect: UKEffectRecord,
    original_targets_str: list[str],
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    source_parent_substitution_range_payload: Optional[dict[str, Any]],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> UKReplacePrelude:
    targets_str = list(original_targets_str)
    trailing_repeal_refs: list[str] = []
    replacement_leaf_override: Optional[str] = None
    replacement_leaf_kind: Optional[str] = None

    # Keep replacement target labels authoritative. The older anchor-retarget
    # heuristic rewrites live replacement labels back to the legacy anchor
    # series, which is exactly the compatibility slop LawVM must not hide.
    label_changing_substitutions = _source_label_changing_substitution_series(
        effect.effect_type,
        original_targets_str,
    )
    if label_changing_substitutions:
        targets_str = [substitution.source_ref for substitution in label_changing_substitutions]
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id=_UK_SOURCE_LABEL_CHANGING_SUBSTITUTION_RULE_ID,
            family="lineage_normalization",
            reason_code="substituted_for_old_sibling_with_new_payload_label",
            reason=(
                "UK source says a labelled sibling is substituted for an "
                "existing sibling, while effect metadata names the new "
                "payload label; lowering keeps the executable replace "
                "target on the source-named old sibling and preserves the "
                "new payload label as the replacement identity."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "substitutions": [
                    {
                        "source_ref": substitution.source_ref,
                        "source_target": str(substitution.source_target),
                        "replacement_ref": substitution.replacement_ref,
                        "replacement_target": str(substitution.replacement_target),
                    }
                    for substitution in label_changing_substitutions
                ],
            },
        )

    if source_parent_substitution_range_payload is not None:
        trailing_repeal_refs = list(source_parent_substitution_range_payload["trailing_refs"])
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id=_UK_SOURCE_PARENT_SUBSTITUTION_RANGE_PAYLOAD_RULE_ID,
            family="source_context_elaboration",
            reason_code="payload_fragment_combined_with_parent_substitution_range",
            reason=(
                "UK effect feed row has no effect type and the extracted "
                "BlockAmendment contains only the replacement payload, but "
                "the source-local parent instruction explicitly substitutes "
                "a bounded sibling range; lowering combines those facts into "
                "one source-owned replacement plus explicit trailing repeals."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                key: value
                for key, value in source_parent_substitution_range_payload.items()
                if key != "rule_id"
            },
        )
    else:
        trailing_repeal_refs = _repeal_tail_for_substituted_series_replacement(
            effect.effect_type,
            original_targets_str,
        )

    if (
        trailing_repeal_refs
        and original_targets_str
        and not label_changing_substitutions
        and source_parent_substitution_range_payload is None
    ):
        try:
            replacement_target = _parse_affected_target(original_targets_str[0])
        except Exception:
            replacement_target = None
        if replacement_target is not None:
            replacement_leaf_override = _addr_leaf_label(replacement_target)
            replacement_leaf_kind = _addr_leaf_kind(replacement_target)

    return UKReplacePrelude(
        targets_str=targets_str,
        trailing_repeal_refs=trailing_repeal_refs,
        replacement_leaf_override=replacement_leaf_override,
        replacement_leaf_kind=replacement_leaf_kind,
        label_changing_substitutions=label_changing_substitutions,
    )
