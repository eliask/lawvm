"""Cross-heading target preprocessing for UK effect lowering."""

from __future__ import annotations

from dataclasses import dataclass
import xml.etree.ElementTree as ET
from typing import Any, Optional

from lawvm.core.ir import LegalAddress
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.heading_facets import (
    _crossheading_and_structural_repeal_selector,
    _crossheading_before_anchor_replacement_text,
    _crossheading_before_anchor_text_patch_fragment,
    _is_crossheading_ref,
)
from lawvm.uk_legislation.lowering_records import _append_uk_effect_lowering_rejection
from lawvm.uk_legislation.source_payload_elaboration import (
    _crossheading_and_structural_replacement_heading_text,
)


@dataclass(frozen=True)
class UKCrossheadingContext:
    replacement_text: Optional[str]
    text_patch_fragment: Optional[dict[str, str]]
    compound_heading_text: Optional[str]
    group_repeal_selector: Optional[dict[str, Any]]


def build_crossheading_context(
    *,
    effect: UKEffectRecord,
    action: str,
    t_str: str,
    target: LegalAddress,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
) -> UKCrossheadingContext:
    is_crossheading = _is_crossheading_ref(t_str)
    return UKCrossheadingContext(
        replacement_text=(
            _crossheading_before_anchor_replacement_text(extracted_text)
            if action == "replace" and is_crossheading
            else None
        ),
        text_patch_fragment=(
            _crossheading_before_anchor_text_patch_fragment(extracted_text)
            if action == "replace" and is_crossheading
            else None
        ),
        compound_heading_text=(
            _crossheading_and_structural_replacement_heading_text(
                affected_ref=t_str,
                extracted_el=extracted_el,
                target=target,
            )
            if action == "replace" and is_crossheading
            else None
        ),
        group_repeal_selector=(
            _crossheading_and_structural_repeal_selector(
                affected_ref=t_str,
                effect_type=effect.effect_type,
                extracted_text=extracted_text,
                target=target,
            )
            if action in {"replace", "repeal"} and is_crossheading
            else None
        ),
    )


def reject_unsupported_crossheading_replace(
    *,
    effect: UKEffectRecord,
    action: str,
    t_str: str,
    context: UKCrossheadingContext,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> bool:
    if not (
        action == "replace"
        and _is_crossheading_ref(t_str)
        and context.replacement_text is None
        and context.text_patch_fragment is None
        and context.compound_heading_text is None
        and context.group_repeal_selector is None
    ):
        return False
    _append_uk_effect_lowering_rejection(
        lowering_rejections_out,
        rule_id="uk_effect_crossheading_replace_rejected",
        family="unsupported_target_facet",
        reason_code="crossheading_replace_unsupported",
        reason=(
            "UK cross-heading replacement target lacks an explicit "
            "heading-before-anchor replacement shape"
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={"target_ref": t_str},
    )
    return True
