"""Cross-heading target preprocessing for UK effect lowering."""

from __future__ import annotations

from dataclasses import dataclass
import xml.etree.ElementTree as ET
from typing import Any, Optional

from lawvm.core.ir import LegalAddress, LegalOperation, OperationSource, TextPatchSpec, TextSelector
from lawvm.core.semantic_types import FacetKind, StructuralAction, TextPatchKindEnum
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.heading_facets import (
    _CROSSHEADING_AND_STRUCTURAL_REPEAL_RULE,
    _CROSSHEADING_AND_STRUCTURAL_REPLACEMENT_SPLIT_RULE,
    _CROSSHEADING_BEFORE_ANCHOR_REPLACEMENT_RULE,
    _crossheading_and_structural_repeal_selector,
    _crossheading_before_anchor_replacement_text,
    _crossheading_before_anchor_text_patch_fragment,
    _heading_facet_after_anchor_insert_fragment,
    _heading_facet_append_fragment,
    _heading_facet_full_replacement_fragment,
    _is_crossheading_ref,
)
from lawvm.uk_legislation.lowering_records import _append_uk_effect_lowering_rejection
from lawvm.uk_legislation.lowering_records import _append_uk_effect_lowering_observation
from lawvm.uk_legislation.source_payload_elaboration import (
    _crossheading_and_structural_replacement_heading_text,
)
from lawvm.uk_legislation.witness_builders import (
    _uk_target_expansion_witness,
    _uk_temporal_group_id,
    _uk_text_rewrite_spec,
)
from lawvm.uk_legislation.witness_sidecars import _uk_lowered_op_provenance_tags
from lawvm.uk_legislation.witnesses import (
    UKEffectWitness,
    UKLoweredOperationWitness,
    UKProvisionExtractionWitness,
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


def refine_crossheading_or_heading_facet_target(
    *,
    effect: UKEffectRecord,
    t_str: str,
    target: LegalAddress,
    heading_facet_target: bool,
    crossheading_replacement_text: Optional[str],
    crossheading_text_patch_fragment: Optional[dict[str, str]],
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> LegalAddress:
    """Attach typed heading facet ownership and emit lowering observations."""
    refined_target = target
    if crossheading_replacement_text is not None:
        refined_target = LegalAddress(path=refined_target.path, special=FacetKind.HEADING)
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id="uk_effect_crossheading_before_anchor_replacement_lowered",
            family="target_facet_lowering",
            reason_code="explicit_crossheading_before_anchor_replacement",
            reason=(
                "UK cross-heading replacement lowered as a typed heading "
                "facet text patch anchored by the named following provision"
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": t_str,
                "target": str(refined_target),
                "replacement_text_preview": crossheading_replacement_text[:200],
            },
        )
    if crossheading_text_patch_fragment is not None:
        refined_target = LegalAddress(path=refined_target.path, special=FacetKind.HEADING)
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id="uk_effect_crossheading_before_anchor_text_patch_lowered",
            family="target_facet_lowering",
            reason_code="explicit_crossheading_before_anchor_text_patch",
            reason=(
                "UK cross-heading replacement lowered as a typed heading "
                "facet text patch anchored by the named following provision"
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": t_str,
                "target": str(refined_target),
                "match_text": str(crossheading_text_patch_fragment["original"]),
                "replacement_text_preview": str(
                    crossheading_text_patch_fragment["replacement"]
                )[:200],
            },
        )
    if not heading_facet_target:
        return refined_target

    refined_target = LegalAddress(path=refined_target.path, special=FacetKind.HEADING)
    heading_append_fragment = _heading_facet_append_fragment(extracted_text)
    heading_after_anchor_insert_fragment = _heading_facet_after_anchor_insert_fragment(
        extracted_text
    )
    heading_full_replacement_fragment = _heading_facet_full_replacement_fragment(
        extracted_text
    )
    if heading_append_fragment is not None:
        heading_observation_rule = "uk_effect_heading_facet_append_lowered"
        heading_reason_code = "explicit_heading_facet_append"
        heading_reason = (
            "UK heading/title/sidenote target lowered as a typed facet "
            "append; replay must mutate only the heading carrier."
        )
    elif heading_after_anchor_insert_fragment is not None:
        heading_observation_rule = "uk_effect_heading_facet_after_anchor_insert_lowered"
        heading_reason_code = "explicit_heading_facet_after_anchor_insert"
        heading_reason = (
            "UK heading/title/sidenote target lowered as a facet text "
            "insertion after an explicit heading anchor; replay must "
            "mutate only the heading carrier."
        )
    elif heading_full_replacement_fragment is not None:
        heading_observation_rule = "uk_effect_heading_facet_full_replacement_lowered"
        heading_reason_code = "explicit_heading_facet_full_replacement"
        heading_reason = (
            "UK heading/title/sidenote target lowered as a full facet "
            "replacement; replay must mutate only the heading carrier."
        )
    else:
        heading_observation_rule = "uk_effect_heading_facet_word_patch_lowered"
        heading_reason_code = "explicit_heading_facet_word_patch"
        heading_reason = (
            "UK heading/title/sidenote target lowered as a facet "
            "text patch; replay must mutate only the heading carrier."
        )
    _append_uk_effect_lowering_observation(
        lowering_rejections_out,
        rule_id=heading_observation_rule,
        family="target_facet_lowering",
        reason_code=heading_reason_code,
        reason=heading_reason,
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={"target_ref": t_str, "target": str(refined_target)},
    )
    return refined_target


def append_crossheading_group_repeal_observation(
    *,
    effect: UKEffectRecord,
    crossheading_group_repeal_selector: dict[str, Any],
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> None:
    _append_uk_effect_lowering_observation(
        lowering_rejections_out,
        rule_id=_CROSSHEADING_AND_STRUCTURAL_REPEAL_RULE,
        family="target_facet_lowering",
        reason_code="explicit_crossheading_and_structural_repeal",
        reason=(
            "UK source explicitly repeals the named provision and the "
            "heading above it; lowering keeps the provision target and "
            "carries a replay selector that may remove the heading "
            "wrapper only if that wrapper owns exactly the target."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail=dict(crossheading_group_repeal_selector),
    )


def build_crossheading_compound_heading_op(
    *,
    effect: UKEffectRecord,
    t_str: str,
    target: LegalAddress,
    replacement_text: str,
    sequence: int,
    effect_witness: UKEffectWitness,
    extraction_witness: UKProvisionExtractionWitness,
    original_targets_str: list[str],
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> LegalOperation:
    heading_target = LegalAddress(path=target.path, special=FacetKind.HEADING)
    _append_uk_effect_lowering_observation(
        lowering_rejections_out,
        rule_id=_CROSSHEADING_AND_STRUCTURAL_REPLACEMENT_SPLIT_RULE,
        family="target_facet_lowering",
        reason_code="explicit_crossheading_and_structural_replacement_split",
        reason=(
            "UK source replaces a provision and its cross-heading from a "
            "single titled payload; lowering emits a separate heading "
            "facet patch and leaves the structural payload on the named "
            "provision target."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            "target_ref": t_str,
            "structural_target": str(target),
            "heading_target": str(heading_target),
            "replacement_text_preview": replacement_text[:200],
        },
    )
    fragment_subs_for_heading = [
        {
            "original": "TEXT_ALL",
            "replacement": replacement_text,
            "rule_id": _CROSSHEADING_BEFORE_ANCHOR_REPLACEMENT_RULE,
        }
    ]
    heading_text_patch = TextPatchSpec(
        kind=TextPatchKindEnum.REPLACE,
        selector=TextSelector(match_text="TEXT_ALL", occurrence=0),
        replacement=replacement_text,
    )
    src = OperationSource(
        statute_id=effect.affecting_act_id,
        title=effect.affecting_title,
        effective=effect_witness.applicability.effective_date or "",
        raw_text=extraction_witness.extracted_text,
    )
    target_expansion_witness = _uk_target_expansion_witness(
        t_str,
        [t_str],
        original_targets_str=original_targets_str,
    )
    text_rewrite_witness = _uk_text_rewrite_spec(
        fragment_subs=fragment_subs_for_heading,
        text_patch=heading_text_patch,
        op_text_match="TEXT_ALL",
        op_text_replacement=replacement_text,
        op_text_occurrence=0,
    )
    lowered_witness = UKLoweredOperationWitness(
        op_id=f"{effect.effect_id}_crossheading",
        sequence=sequence,
        action=StructuralAction.TEXT_REPLACE,
        target=heading_target,
        payload=None,
        source=src,
        effect_witness=effect_witness,
        extraction_witness=extraction_witness,
        target_expansion_witness=target_expansion_witness,
        text_rewrite_witness=text_rewrite_witness,
        insertion_anchor_witness=None,
    )
    return LegalOperation(
        op_id=lowered_witness.op_id,
        sequence=lowered_witness.sequence,
        action=lowered_witness.action,
        target=lowered_witness.target,
        payload=None,
        source=lowered_witness.source,
        group_id=_uk_temporal_group_id(effect),
        provenance_tags=_uk_lowered_op_provenance_tags(lowered_witness),
        text_patch=heading_text_patch,
        witness_rule_id=_CROSSHEADING_AND_STRUCTURAL_REPLACEMENT_SPLIT_RULE,
    )
