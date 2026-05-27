"""Cross-heading target preprocessing for UK effect lowering."""

from __future__ import annotations

from dataclasses import dataclass
import re
import xml.etree.ElementTree as ET
from typing import Any, Optional

from lawvm.core.ir import LegalAddress, LegalOperation, OperationSource, TextPatchSpec, TextSelector
from lawvm.core.semantic_types import FacetKind, StructuralAction, TextPatchKindEnum
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.heading_facets import (
    _CROSSHEADING_AND_STRUCTURAL_REPEAL_RULE,
    _CROSSHEADING_AND_STRUCTURAL_REPLACEMENT_SPLIT_RULE,
    _CROSSHEADING_BEFORE_ANCHOR_REPLACEMENT_RULE,
    _CROSSHEADING_TARGET_REPLACEMENT_RULE,
    _CROSSHEADING_SOURCE_PARENT_REFERENCE_SUBSTITUTION_RULE,
    _CROSSHEADING_SOURCE_PARENT_TAIL_SUBSTITUTION_RULE,
    _crossheading_and_structural_repeal_selector,
    _crossheading_before_anchor_replacement_text,
    _crossheading_before_anchor_text_patch_fragment,
    _crossheading_metadata_target_deictic_text_patch_fragment,
    _crossheading_target_replacement_text,
    _heading_facet_after_anchor_insert_fragment,
    _heading_facet_append_fragment,
    _heading_facet_full_replacement_fragment,
    _heading_facet_source_parent_full_replacement_fragment,
    _is_crossheading_ref,
    _is_heading_only_ref,
)
from lawvm.uk_legislation.lowering_records import _append_uk_effect_lowering_rejection
from lawvm.uk_legislation.lowering_records import _append_uk_effect_lowering_observation
from lawvm.uk_legislation.source_payload_elaboration import (
    _crossheading_and_structural_replacement_heading_text,
)
from lawvm.uk_legislation.source_context import (
    _source_ancestor_chain,
    _unique_source_ancestor_chain_by_tag_text,
)
from lawvm.uk_legislation.source_fragment_context import (
    _source_lead_text_before_subordinate_rows,
    _source_tail_text_after_subordinate_rows,
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
    replacement_rule_id: Optional[str]
    replacement_observation_rule_id: Optional[str]
    replacement_reason_code: Optional[str]
    replacement_reason: Optional[str]
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
    source_root: Optional[ET.Element],
) -> UKCrossheadingContext:
    is_crossheading = _is_crossheading_ref(t_str)
    before_anchor_replacement_text = (
        _crossheading_before_anchor_replacement_text(extracted_text)
        if action == "replace" and is_crossheading
        else None
    )
    target_replacement_text = (
        _crossheading_target_replacement_text(extracted_text)
        if action == "replace" and is_crossheading and before_anchor_replacement_text is None
        else None
    )
    replacement_text = before_anchor_replacement_text or target_replacement_text
    if before_anchor_replacement_text is not None:
        replacement_rule_id = _CROSSHEADING_BEFORE_ANCHOR_REPLACEMENT_RULE
        replacement_observation_rule_id = "uk_effect_crossheading_before_anchor_replacement_lowered"
        replacement_reason_code = "explicit_crossheading_before_anchor_replacement"
        replacement_reason = (
            "UK cross-heading replacement lowered as a typed heading "
            "facet text patch anchored by the named following provision"
        )
    elif target_replacement_text is not None:
        replacement_rule_id = _CROSSHEADING_TARGET_REPLACEMENT_RULE
        replacement_observation_rule_id = "uk_effect_crossheading_target_replacement_lowered"
        replacement_reason_code = "explicit_crossheading_target_replacement"
        replacement_reason = (
            "UK effect metadata targets a cross-heading and the source "
            "contains a full heading substitution; lowering preserves the "
            "target as a typed heading facet text patch."
        )
    else:
        replacement_rule_id = None
        replacement_observation_rule_id = None
        replacement_reason_code = None
        replacement_reason = None
    text_patch_fragment = (
        _crossheading_before_anchor_text_patch_fragment(extracted_text)
        if action == "replace" and is_crossheading
        else None
    )
    if text_patch_fragment is None and action == "replace" and is_crossheading:
        text_patch_fragment = _crossheading_metadata_target_deictic_text_patch_fragment(
            extracted_text,
            target,
        )
    if text_patch_fragment is None and action == "replace" and is_crossheading:
        text_patch_fragment = _crossheading_source_parent_reference_text_patch_fragment(
            extracted_el=extracted_el,
            source_root=source_root,
            extracted_text=extracted_text,
        )
    if text_patch_fragment is None and action == "replace" and is_crossheading:
        text_patch_fragment = _crossheading_source_parent_tail_text_patch_fragment(
            extracted_el=extracted_el,
            source_root=source_root,
            extracted_text=extracted_text,
            target=target,
        )
    return UKCrossheadingContext(
        replacement_text=replacement_text,
        replacement_rule_id=replacement_rule_id,
        replacement_observation_rule_id=replacement_observation_rule_id,
        replacement_reason_code=replacement_reason_code,
        replacement_reason=replacement_reason,
        text_patch_fragment=text_patch_fragment,
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


def _crossheading_source_parent_reference_text_patch_fragment(
    *,
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
    extracted_text: Optional[str],
) -> Optional[dict[str, str]]:
    """Resolve listed cross-heading rows from a parent reference substitution."""
    child_text = " ".join((extracted_text or "").split())
    if not re.search(
        r"\b(?:cross-heading|cross heading|heading)\s+before\s+section\s+[0-9A-Za-z]+\b",
        child_text,
        flags=re.I,
    ):
        return None
    ancestors = _source_ancestor_chain(source_root, extracted_el)
    if not ancestors:
        ancestors = _unique_source_ancestor_chain_by_tag_text(source_root, extracted_el)
    for ancestor in ancestors:
        parent_text = _source_lead_text_before_subordinate_rows(ancestor)
        if not parent_text:
            continue
        match = re.search(
            r"\bfor\s+a\s+reference\s+to\s+the\s+(?P<original>.+?)\s+"
            r"substitute\s+a\s+reference\s+to\s+the\s+(?P<replacement>.+?)\s*[—–-]\s*$",
            parent_text,
            flags=re.I,
        )
        if match is None:
            continue
        original = " ".join(match.group("original").split()).strip()
        replacement = " ".join(match.group("replacement").split()).strip()
        if not original or not replacement:
            return None
        return {
            "original": original,
            "replacement": replacement,
            "source_parent_id": str(ancestor.get("id") or ""),
            "rule_id": _CROSSHEADING_SOURCE_PARENT_REFERENCE_SUBSTITUTION_RULE,
        }
    return None


def _crossheading_source_parent_tail_text_patch_fragment(
    *,
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
    extracted_text: Optional[str],
    target: LegalAddress,
) -> Optional[dict[str, str]]:
    """Resolve listed cross-heading child rows governed by a parent tail substitution."""
    target_label = str(target.path[-1][1] if target.path else "").strip()
    if not target_label:
        return None
    child_text = " ".join((extracted_text or "").split())
    match = re.search(
        r"\bfor\s+[“\"'‘](?P<original>.+?)[”\"'’]\s*,?\s*"
        r"(?:in\s+each\s+place\s+)?(?:it\s+)?occurs\b",
        child_text,
        flags=re.I,
    )
    if match is None:
        return None
    original = " ".join(match.group("original").split()).strip()
    if not original:
        return None
    ancestors = _source_ancestor_chain(source_root, extracted_el)
    if not ancestors:
        ancestors = _unique_source_ancestor_chain_by_tag_text(source_root, extracted_el)
    for ancestor in ancestors:
        lead_text = _source_lead_text_before_subordinate_rows(ancestor)
        if not re.search(
            rf"\b(?:italic\s+)?(?:heading|cross-heading|cross heading)\s+before\s+"
            rf"paragraph\s+{re.escape(target_label)}\b",
            lead_text,
            flags=re.I,
        ):
            continue
        tail_text = _source_tail_text_after_subordinate_rows(ancestor)
        tail_match = re.search(
            r"\bsubstitute\s+[“\"'‘](?P<replacement>.+?)[”\"'’]\s*\.?$",
            tail_text,
            flags=re.I,
        )
        if tail_match is None:
            continue
        replacement = " ".join(tail_match.group("replacement").split()).strip()
        if not replacement:
            return None
        return {
            "original": original,
            "replacement": replacement,
            "source_parent_id": str(ancestor.get("id") or ""),
            "rule_id": _CROSSHEADING_SOURCE_PARENT_TAIL_SUBSTITUTION_RULE,
            "source_context": "source_parent_tail_substitution",
        }
    return None


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


def reject_crossheading_source_without_crossheading_target(
    *,
    effect: UKEffectRecord,
    action: str,
    t_str: str,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> bool:
    """Block generic text lowering for source text that names a cross-heading facet."""
    if action != "replace" or _is_crossheading_ref(t_str) or _is_heading_only_ref(t_str):
        return False
    text = " ".join((extracted_text or "").split())
    if not re.search(
        r"\b(?:in\s+the\s+)?(?:cross-heading|cross heading|heading)\s+before\s+"
        r"(?:that\s+)?(?:paragraph|section|article)\b",
        text,
        flags=re.I,
    ):
        return False
    _append_uk_effect_lowering_rejection(
        lowering_rejections_out,
        rule_id="uk_effect_crossheading_source_target_mismatch_rejected",
        family="unsupported_target_facet",
        reason_code="crossheading_source_requires_crossheading_target",
        reason=(
            "UK source text names a cross-heading facet, but effect metadata "
            "does not target a cross-heading; lowering must not apply the "
            "quoted substitution to the host provision body."
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
    crossheading_replacement_observation_rule_id: Optional[str],
    crossheading_replacement_reason_code: Optional[str],
    crossheading_replacement_reason: Optional[str],
    crossheading_text_patch_fragment: Optional[dict[str, str]],
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> LegalAddress:
    """Attach typed heading facet ownership and emit lowering observations."""
    refined_target = target
    if crossheading_replacement_text is not None:
        refined_target = LegalAddress(path=refined_target.path, special=FacetKind.HEADING)
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id=(
                crossheading_replacement_observation_rule_id
                or "uk_effect_crossheading_before_anchor_replacement_lowered"
            ),
            family="target_facet_lowering",
            reason_code=(
                crossheading_replacement_reason_code
                or "explicit_crossheading_before_anchor_replacement"
            ),
            reason=(
                crossheading_replacement_reason
                or (
                    "UK cross-heading replacement lowered as a typed heading "
                    "facet text patch anchored by the named following provision"
                )
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
        fragment_rule_id = str(crossheading_text_patch_fragment.get("rule_id") or "")
        source_parent_rule = fragment_rule_id in {
            _CROSSHEADING_SOURCE_PARENT_REFERENCE_SUBSTITUTION_RULE,
            _CROSSHEADING_SOURCE_PARENT_TAIL_SUBSTITUTION_RULE,
        }
        observation_rule_id = (
            fragment_rule_id
            if source_parent_rule
            else "uk_effect_crossheading_before_anchor_text_patch_lowered"
        )
        reason_code = (
            "crossheading_text_patch_resolved_from_source_parent"
            if source_parent_rule
            else "explicit_crossheading_before_anchor_text_patch"
        )
        reason = (
            "UK source child row identifies a cross-heading target while its parent "
            "list instruction carries the substitution; lowering combines "
            "those source-local facts instead of mutating the host provision body."
            if source_parent_rule
            else (
                "UK cross-heading replacement lowered as a typed heading "
                "facet text patch anchored by the named following provision"
            )
        )
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id=observation_rule_id,
            family="target_facet_lowering",
            reason_code=reason_code,
            reason=reason,
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": t_str,
                "target": str(refined_target),
                "source_parent_id": str(crossheading_text_patch_fragment.get("source_parent_id") or ""),
                "source_context": str(crossheading_text_patch_fragment.get("source_context") or ""),
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
    heading_effect_type = " ".join(str(effect.effect_type or "").lower().split())
    allow_source_parent_full_replacement = heading_effect_type not in {
        "word substituted",
        "words substituted",
        "word omitted",
        "words omitted",
        "word inserted",
        "words inserted",
    }
    heading_source_parent_full_replacement_fragment = (
        _heading_facet_source_parent_full_replacement_fragment(
            extracted_el=extracted_el,
            source_root=source_root,
        )
        if allow_source_parent_full_replacement
        else None
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
    elif heading_source_parent_full_replacement_fragment is not None:
        heading_observation_rule = (
            "uk_effect_heading_facet_source_parent_full_replacement_lowered"
        )
        heading_reason_code = "heading_replacement_resolved_from_source_parent"
        heading_reason = (
            "UK source payload carries only inserted body provisions, while "
            "its parent instruction carries the heading/title replacement; "
            "lowering mutates only the heading carrier."
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
