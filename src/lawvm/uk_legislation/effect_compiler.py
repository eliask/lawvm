"""Single-effect lowering entry point for UK amendment replay."""

from __future__ import annotations

import xml.etree.ElementTree as ET
import time
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
    lower_uk_after_section_subsection_range_insert_block_amendment,
    lower_uk_after_paragraph_insert_connector_sibling,
    lower_uk_after_paragraph_insert_labelled_series,
    lower_uk_after_paragraph_insert_single_label,
    lower_uk_definition_child_structural_sibling_insert,
    lower_uk_definition_child_range_substitution,
    lower_uk_metadata_renumber_effect,
    lower_uk_source_carried_structured_tail_substitution,
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
from lawvm.uk_legislation.lowering_records import _append_uk_effect_lowering_rejection
from lawvm.uk_legislation.metadata_rewrites import (
    _uk_affected_target_corrected_renumber_targets,
    _uk_metadata_renumber_targets,
    _uk_source_text_corrected_renumber_targets,
)
from lawvm.uk_legislation.source_action_inference import (
    append_no_supported_action_rejection,
    infer_uk_effect_action_from_source,
)
from lawvm.uk_legislation.source_parent_payloads import (
    _source_at_end_section_subsection_insert_block_amendment,
    _source_after_section_subsection_range_insert_block_amendment,
    _source_after_paragraph_insert_block_amendment,
    _source_after_paragraph_insert_connector_sibling,
    _source_after_paragraph_insert_labelled_series,
    _source_after_paragraph_insert_single_label,
    _source_carried_structured_tail_substitution,
)
from lawvm.uk_legislation.source_definition_fragments import (
    source_definition_child_range_substitution,
)
from lawvm.uk_legislation.source_definition_structural_insert import (
    source_definition_child_structural_sibling_insert,
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
from lawvm.uk_legislation.effect_target_prelude import canonicalize_uk_address
from lawvm.uk_legislation.target_parser import _parse_affected_target
from lawvm.uk_legislation.table_sources import (
    _uk_table_driven_fee_target_refinements,
    address_to_citation,
)



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
    lower_phase_timings_out: Optional[dict[str, float]] = None,
) -> list[LegalOperation]:
    """Compile a UKEffectRecord + XML element into LawVM LegalOperations.

    Word-level effects lower to typed text-patch operations. Structural effects
    lower to canonical replace/repeal/insert operations only when source and
    target evidence support that action family.
    """
    phase_t0 = time.perf_counter()

    def _mark_lower_phase(name: str) -> None:
        nonlocal phase_t0
        now = time.perf_counter()
        if lower_phase_timings_out is not None:
            lower_phase_timings_out[name] = lower_phase_timings_out.get(name, 0.0) + (
                now - phase_t0
            )
        phase_t0 = now

    effect_type = (effect.effect_type or "").strip().lower()
    extracted_text = _text_content(extracted_el) if extracted_el is not None else None
    metadata_renumber_targets = _uk_metadata_renumber_targets(effect)
    if metadata_renumber_targets is None:
        metadata_renumber_targets = _uk_affected_target_corrected_renumber_targets(
            effect,
            extracted_text,
        )

    if effect_type in _COMMENCEMENT_EFFECT_TYPES:
        _mark_lower_phase("compile_lower_prepare")
        return []

    is_word_level = _is_uk_word_level_effect_type(effect_type)
    action = _uk_effect_type_action(
        effect_type,
        has_metadata_renumber_targets=metadata_renumber_targets is not None,
    )
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
        _mark_lower_phase("compile_lower_prepare")
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
        _mark_lower_phase("compile_lower_prepare")
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
    _mark_lower_phase("compile_lower_prepare")

    if action == "renumber" and metadata_renumber_targets is not None:
        ops = lower_uk_metadata_renumber_effect(
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            sequence=sequence,
            metadata_renumber_targets=metadata_renumber_targets,
            effect_witness=effect_witness,
            extraction_witness=extraction_witness,
            lowering_rejections_out=lowering_rejections_out,
        )
        _mark_lower_phase("compile_lower_special")
        return ops

    definition_child_range = source_definition_child_range_substitution(
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        affected_provisions=effect.affected_provisions,
    )
    if action == "replace" and definition_child_range is not None:
        ops = lower_uk_definition_child_range_substitution(
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            sequence=sequence,
            definition_child_range=definition_child_range,
            effect_witness=effect_witness,
            extraction_witness=extraction_witness,
            lowering_rejections_out=lowering_rejections_out,
        )
        _mark_lower_phase("compile_lower_special")
        return ops

    definition_child_structural_insert = source_definition_child_structural_sibling_insert(
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        source_root=source_root,
        affected_provisions=effect.affected_provisions,
    )
    if action == "insert" and definition_child_structural_insert is not None:
        if definition_child_structural_insert.get("blocking"):
            _append_uk_effect_lowering_rejection(
                lowering_rejections_out,
                rule_id=str(definition_child_structural_insert["rule_id"]),
                family=str(definition_child_structural_insert["family"]),
                reason_code=str(definition_child_structural_insert["reason_code"]),
                reason=str(definition_child_structural_insert["reason"]),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    key: value
                    for key, value in definition_child_structural_insert.items()
                    if key not in {"rule_id", "family", "reason_code", "reason"}
                },
            )
            _mark_lower_phase("compile_lower_special")
            return []
        ops = lower_uk_definition_child_structural_sibling_insert(
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            sequence=sequence,
            definition_child_insert=definition_child_structural_insert,
            effect_witness=effect_witness,
            extraction_witness=extraction_witness,
            lowering_rejections_out=lowering_rejections_out,
        )
        _mark_lower_phase("compile_lower_special")
        return ops

    after_paragraph_series = _source_after_paragraph_insert_labelled_series(
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        affected_provisions=effect.affected_provisions,
    )
    if action == "insert" and after_paragraph_series is not None:
        ops = lower_uk_after_paragraph_insert_labelled_series(
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            sequence=sequence,
            after_paragraph_series=after_paragraph_series,
            effect_witness=effect_witness,
            extraction_witness=extraction_witness,
            lowering_rejections_out=lowering_rejections_out,
        )
        _mark_lower_phase("compile_lower_special")
        return ops
    after_paragraph_connector = _source_after_paragraph_insert_connector_sibling(
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        affected_provisions=effect.affected_provisions,
    )
    if action == "insert" and after_paragraph_connector is not None:
        ops = lower_uk_after_paragraph_insert_connector_sibling(
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            sequence=sequence,
            after_paragraph_connector=after_paragraph_connector,
            effect_witness=effect_witness,
            extraction_witness=extraction_witness,
            lowering_rejections_out=lowering_rejections_out,
        )
        _mark_lower_phase("compile_lower_special")
        return ops
    after_paragraph_block_insert = _source_after_paragraph_insert_block_amendment(
        extracted_el=extracted_el,
        affected_provisions=effect.affected_provisions,
    )
    if action == "insert" and after_paragraph_block_insert is not None:
        ops = lower_uk_after_paragraph_insert_single_label(
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            sequence=sequence,
            after_paragraph_insert=after_paragraph_block_insert,
            effect_witness=effect_witness,
            extraction_witness=extraction_witness,
            lowering_rejections_out=lowering_rejections_out,
        )
        _mark_lower_phase("compile_lower_special")
        return ops
    after_paragraph_insert = _source_after_paragraph_insert_single_label(
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        affected_provisions=effect.affected_provisions,
    )
    if action == "insert" and after_paragraph_insert is not None:
        ops = lower_uk_after_paragraph_insert_single_label(
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            sequence=sequence,
            after_paragraph_insert=after_paragraph_insert,
            effect_witness=effect_witness,
            extraction_witness=extraction_witness,
            lowering_rejections_out=lowering_rejections_out,
        )
        _mark_lower_phase("compile_lower_special")
        return ops
    after_section_subsection_range_insert = (
        _source_after_section_subsection_range_insert_block_amendment(
            extracted_el=extracted_el,
            affected_provisions=effect.affected_provisions,
        )
    )
    if action == "insert" and after_section_subsection_range_insert is not None:
        ops = lower_uk_after_section_subsection_range_insert_block_amendment(
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            sequence=sequence,
            after_section_subsection_range_insert=after_section_subsection_range_insert,
            effect_witness=effect_witness,
            extraction_witness=extraction_witness,
            lowering_rejections_out=lowering_rejections_out,
        )
        _mark_lower_phase("compile_lower_special")
        return ops
    at_end_section_subsection_insert = (
        _source_at_end_section_subsection_insert_block_amendment(
            extracted_el=extracted_el,
            affected_provisions=effect.affected_provisions,
        )
    )
    if action == "insert" and at_end_section_subsection_insert is not None:
        ops = lower_uk_after_section_subsection_range_insert_block_amendment(
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            sequence=sequence,
            after_section_subsection_range_insert=at_end_section_subsection_insert,
            effect_witness=effect_witness,
            extraction_witness=extraction_witness,
            lowering_rejections_out=lowering_rejections_out,
        )
        _mark_lower_phase("compile_lower_special")
        return ops
    structured_tail_substitution = _source_carried_structured_tail_substitution(
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        affected_provisions=effect.affected_provisions,
        affecting_provisions=effect.affecting_provisions,
    )
    if action in {"insert", "replace", "text_replace"} and structured_tail_substitution is not None:
        ops = lower_uk_source_carried_structured_tail_substitution(
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            sequence=sequence,
            structured_tail_substitution=structured_tail_substitution,
            effect_witness=effect_witness,
            extraction_witness=extraction_witness,
            lowering_rejections_out=lowering_rejections_out,
        )
        _mark_lower_phase("compile_lower_special")
        return ops

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
    _mark_lower_phase("compile_lower_target_prelude")
    if target_prelude is None:
        return []
    targets_str = target_prelude.targets_str
    refined_targets_str = []
    for t_str in targets_str:
        try:
            parsed_target = _parse_affected_target(t_str)
            target = canonicalize_uk_address(parsed_target)
            refinement_addresses = _uk_table_driven_fee_target_refinements(
                effect=effect,
                source_root=source_root,
                target=target,
            )
            if refinement_addresses:
                for ref_target in refinement_addresses:
                    refined_targets_str.append(address_to_citation(ref_target))
            else:
                refined_targets_str.append(t_str)
        except Exception:
            refined_targets_str.append(t_str)
    targets_str = refined_targets_str
    original_targets_str = list(targets_str)
    mixed_heading_source_ref_by_target = target_prelude.mixed_heading_source_ref_by_target
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
    _mark_lower_phase("compile_lower_target_setup")
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
    _mark_lower_phase("compile_lower_targets")
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
            source_root=source_root,
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
    _mark_lower_phase("compile_lower_tail")
    return ops
