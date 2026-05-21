"""Single-effect lowering entry point for UK amendment replay."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Optional

from lawvm.core.ir import LegalOperation
from lawvm.uk_legislation.effects import UKEffectRecord, _COMMENCEMENT_EFFECT_TYPES
from lawvm.uk_legislation.effect_lowering_tail import (
    append_no_targets_rejection,
    append_source_parent_at_end_added_observation,
    append_unlowered_overlap_substitution_rejection,
    build_crossheading_insert_ops,
    build_trailing_repeal_ops,
)
from lawvm.uk_legislation.effect_replace_prelude import plan_replace_effect_prelude
from lawvm.uk_legislation.effect_single_target_lowering import (
    _ChainedInsertAnchorState,
    _EffectTargetLoweringInput,
    _lower_effect_target,
)
from lawvm.uk_legislation.effect_special_lowering import (
    lower_uk_after_paragraph_insert_labelled_series,
    lower_uk_metadata_renumber_effect,
)
from lawvm.uk_legislation.effect_target_prelude import (
    append_added_type_source_structuralized_observation,
    append_heading_facet_range_expansion_observation,
    expand_single_target_prelude,
)
from lawvm.uk_legislation.lowering_actions import (
    _is_uk_word_level_effect_type,
    _uk_effect_type_action,
)
from lawvm.uk_legislation.metadata_rewrites import (
    _uk_metadata_renumber_targets,
    _uk_source_text_corrected_renumber_targets,
)
from lawvm.uk_legislation.source_action_inference import (
    append_no_supported_action_rejection,
    infer_uk_effect_action_from_source,
)
from lawvm.uk_legislation.source_parent_payloads import (
    _source_after_paragraph_insert_labelled_series,
)
from lawvm.uk_legislation.substitution_metadata import (
    UKSourceLabelChangingSubstitution,
    _source_replaced_sibling_count_from_substitution_text,
)
from lawvm.uk_legislation.target_parser import _split_metadata_provisions
from lawvm.uk_legislation.witness_builders import (
    _uk_effect_witness,
    _uk_extraction_witness,
)
from lawvm.uk_legislation.xml_helpers import _text_content


@dataclass(frozen=True)
class _EffectTargetPrelude:
    targets_str: list[str]
    original_targets_str: list[str]
    mixed_heading_source_ref_by_target: dict[str, str]
    trailing_repeal_refs: list[str]
    replacement_leaf_override: Optional[str]
    replacement_leaf_kind: Optional[str]
    label_changing_substitutions: tuple[UKSourceLabelChangingSubstitution, ...]


def _prepare_effect_target_prelude(
    *,
    effect: UKEffectRecord,
    effect_type: str,
    action: str,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    source_parent_substitution_range_payload: Optional[dict[str, Any]],
    source_parent_at_end_added_payload: Optional[dict[str, Any]],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> _EffectTargetPrelude | None:
    raw_affected_provisions = effect.affected_provisions
    targets_str = _split_metadata_provisions(effect.affected_provisions)
    original_targets_str = list(targets_str)
    append_heading_facet_range_expansion_observation(
        effect=effect,
        raw_affected_provisions=raw_affected_provisions,
        targets_str=targets_str,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        lowering_rejections_out=lowering_rejections_out,
    )
    mixed_heading_source_ref_by_target: dict[str, str] = {}
    trailing_repeal_refs: list[str] = []
    replacement_leaf_override: Optional[str] = None
    replacement_leaf_kind: Optional[str] = None
    label_changing_substitutions: tuple[UKSourceLabelChangingSubstitution, ...] = ()
    if action == "replace":
        replace_prelude = plan_replace_effect_prelude(
            effect=effect,
            original_targets_str=original_targets_str,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            source_parent_substitution_range_payload=source_parent_substitution_range_payload,
            lowering_rejections_out=lowering_rejections_out,
        )
        targets_str = replace_prelude.targets_str
        trailing_repeal_refs = replace_prelude.trailing_repeal_refs
        replacement_leaf_override = replace_prelude.replacement_leaf_override
        replacement_leaf_kind = replace_prelude.replacement_leaf_kind
        label_changing_substitutions = replace_prelude.label_changing_substitutions
    append_source_parent_at_end_added_observation(
        lowering_rejections_out,
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        source_parent_at_end_added_payload=source_parent_at_end_added_payload,
    )
    target_prelude = expand_single_target_prelude(
        effect=effect,
        action=action,
        targets_str=targets_str,
        original_targets_str=original_targets_str,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        lowering_rejections_out=lowering_rejections_out,
    )
    targets_str = target_prelude.targets_str
    mixed_heading_source_ref_by_target = target_prelude.mixed_heading_source_ref_by_target
    append_added_type_source_structuralized_observation(
        effect=effect,
        effect_type=effect_type,
        action=action,
        targets_str=targets_str,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        lowering_rejections_out=lowering_rejections_out,
    )
    if not targets_str:
        append_no_targets_rejection(
            lowering_rejections_out,
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
        )
        return None
    return _EffectTargetPrelude(
        targets_str=targets_str,
        original_targets_str=original_targets_str,
        mixed_heading_source_ref_by_target=mixed_heading_source_ref_by_target,
        trailing_repeal_refs=trailing_repeal_refs,
        replacement_leaf_override=replacement_leaf_override,
        replacement_leaf_kind=replacement_leaf_kind,
        label_changing_substitutions=label_changing_substitutions,
    )


def compile_effect_to_ir_ops(
    effect: UKEffectRecord,
    extracted_el: Optional[ET.Element],
    sequence: int = 0,
    fallback_for_missing_extracted_source: bool = False,
    lowering_rejections_out: Optional[list[dict[str, Any]]] = None,
    allow_payload_identity_synthesis: bool = True,
    source_root: Optional[ET.Element] = None,
    source_authority_layer: str = "",
) -> list[LegalOperation]:
    """Compile a UKEffectRecord + XML element into LawVM LegalOperations.

    Word-level effects lower to typed text-patch operations. Structural effects
    lower to canonical replace/repeal/insert operations only when source and
    target evidence support that action family.
    """
    effect_type = (effect.effect_type or "").strip().lower()
    metadata_renumber_targets = _uk_metadata_renumber_targets(effect)

    if effect_type in _COMMENCEMENT_EFFECT_TYPES:
        return []

    is_word_level = _is_uk_word_level_effect_type(effect_type)
    action = _uk_effect_type_action(
        effect_type,
        has_metadata_renumber_targets=metadata_renumber_targets is not None,
    )
    extracted_text = _text_content(extracted_el) if extracted_el is not None else None
    metadata_renumber_targets = _uk_source_text_corrected_renumber_targets(
        metadata_renumber_targets,
        extracted_text,
    )
    source_parent_substitution_range_payload: Optional[dict[str, Any]] = None
    source_parent_at_end_added_payload: Optional[dict[str, Any]] = None

    action_inference = infer_uk_effect_action_from_source(
        effect=effect,
        effect_type=effect_type,
        initial_action=action,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        source_root=source_root,
        lowering_rejections_out=lowering_rejections_out,
    )
    if action_inference.blocked:
        return []
    action = action_inference.action
    source_parent_substitution_range_payload = (
        action_inference.source_parent_substitution_range_payload
    )
    source_parent_at_end_added_payload = action_inference.source_parent_at_end_added_payload

    if not action:
        append_no_supported_action_rejection(
            effect=effect,
            effect_type=effect_type,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            lowering_rejections_out=lowering_rejections_out,
        )
        return []

    use_metadata_fallback = (
        fallback_for_missing_extracted_source
        and extracted_el is None
        and action == "insert"
        and effect_type not in {"added", "entry inserted"}
    )
    extraction_witness = _uk_extraction_witness(
        effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        metadata_fallback_used=use_metadata_fallback,
        source_authority_layer=source_authority_layer,
    )
    effect_witness = _uk_effect_witness(
        effect,
        authority_layer=extraction_witness.authority_layer,
    )

    if action == "renumber" and metadata_renumber_targets is not None:
        return lower_uk_metadata_renumber_effect(
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            sequence=sequence,
            metadata_renumber_targets=metadata_renumber_targets,
            effect_witness=effect_witness,
            extraction_witness=extraction_witness,
            lowering_rejections_out=lowering_rejections_out,
        )

    after_paragraph_series = _source_after_paragraph_insert_labelled_series(
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        affected_provisions=effect.affected_provisions,
    )
    if action == "insert" and after_paragraph_series is not None:
        return lower_uk_after_paragraph_insert_labelled_series(
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            sequence=sequence,
            after_paragraph_series=after_paragraph_series,
            effect_witness=effect_witness,
            extraction_witness=extraction_witness,
            lowering_rejections_out=lowering_rejections_out,
        )

    target_prelude = _prepare_effect_target_prelude(
        effect=effect,
        effect_type=effect_type,
        action=action,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        source_parent_at_end_added_payload=source_parent_at_end_added_payload,
        source_parent_substitution_range_payload=source_parent_substitution_range_payload,
        lowering_rejections_out=lowering_rejections_out,
    )
    if target_prelude is None:
        return []
    targets_str = target_prelude.targets_str
    mixed_heading_source_ref_by_target = target_prelude.mixed_heading_source_ref_by_target
    original_targets_str = target_prelude.original_targets_str
    trailing_repeal_refs = target_prelude.trailing_repeal_refs
    replacement_leaf_override = target_prelude.replacement_leaf_override
    replacement_leaf_kind = target_prelude.replacement_leaf_kind
    label_changing_substitutions = target_prelude.label_changing_substitutions

    ops = []
    unlowered_overlap_substitution_targets: list[str] = []
    unlowered_overlap_substitution_reason = ""
    chained_insert_anchor = _ChainedInsertAnchorState()
    if action == "insert":
        ops.extend(
            build_crossheading_insert_ops(
                effect=effect,
                extracted_el=extracted_el,
                sequence=sequence,
                effect_witness=effect_witness,
                extraction_witness=extraction_witness,
            )
        )
    source_replaced_sibling_count = (
        _source_replaced_sibling_count_from_substitution_text(
            extracted_text=extracted_text,
            target_refs=targets_str,
        )
        if action == "replace"
        else None
    )
    for target_index, t_str in enumerate(targets_str):
        target_result = _lower_effect_target(
            _EffectTargetLoweringInput(
                effect=effect,
                effect_type=effect_type,
                action=action,
                is_word_level=is_word_level,
                target_ref=t_str,
                targets_str=targets_str,
                original_targets_str=original_targets_str,
                mixed_heading_source_ref_by_target=mixed_heading_source_ref_by_target,
                label_changing_substitutions=label_changing_substitutions,
                replacement_leaf_override=replacement_leaf_override,
                replacement_leaf_kind=replacement_leaf_kind,
                source_parent_substitution_range_payload=source_parent_substitution_range_payload,
                source_parent_at_end_added_payload=source_parent_at_end_added_payload,
                source_replaced_sibling_count=source_replaced_sibling_count,
                use_metadata_fallback=use_metadata_fallback,
                allow_payload_identity_synthesis=allow_payload_identity_synthesis,
                sequence=sequence,
                existing_ops_count=len(ops),
                effect_witness=effect_witness,
                extraction_witness=extraction_witness,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                source_root=source_root,
                chained_insert_anchor=chained_insert_anchor,
                lowering_rejections_out=lowering_rejections_out,
                target_index=target_index,
            )
        )
        ops.extend(target_result.ops)
        chained_insert_anchor = target_result.chained_insert_anchor
        if target_result.unlowered_overlap_reason:
            unlowered_overlap_substitution_targets.append(
                target_result.unlowered_overlap_target
            )
            unlowered_overlap_substitution_reason = (
                target_result.unlowered_overlap_reason
            )
    if not ops and unlowered_overlap_substitution_targets:
        append_unlowered_overlap_substitution_rejection(
            lowering_rejections_out,
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            effect_type=effect_type,
            original_targets_str=original_targets_str,
            target_candidate_count=len(targets_str),
            unlowered_overlap_substitution_targets=unlowered_overlap_substitution_targets,
            unlowered_overlap_substitution_reason=unlowered_overlap_substitution_reason,
        )
    if action == "replace" and trailing_repeal_refs:
        ops.extend(
            build_trailing_repeal_ops(
                effect=effect,
                sequence=sequence,
                trailing_repeal_refs=trailing_repeal_refs,
                effect_witness=effect_witness,
                extraction_witness=extraction_witness,
                original_targets_str=original_targets_str,
                source_parent_substitution_range_payload=source_parent_substitution_range_payload,
            )
        )
    return ops
