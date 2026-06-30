"""Single-effect lowering entry point for UK amendment replay."""

from __future__ import annotations

from lxml import etree as ET
import time
from dataclasses import dataclass, replace as dc_replace
from typing import Any, Optional

from lawvm.core.ir import LegalOperation
from lawvm.core.semantic_types import StructuralAction
from lawvm.uk_legislation.addressing import (
    _action_name,
    _addr_leaf_label,
    _uk_canonicalize_eid_letter_case,
)
from lawvm.uk_legislation.effects import UKEffectRecord, _COMMENCEMENT_EFFECT_TYPES
from lawvm.uk_legislation.effect_lowering_tail import (
    append_no_targets_rejection,
    append_source_parent_at_end_added_observation,
    append_unlowered_overlap_substitution_rejection,
    augment_extracted_text_with_instruction_context,
    build_crossheading_insert_ops,
    build_trailing_repeal_ops,
    source_shape_blocks_before_text_patch_lowering,
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
    lower_uk_definition_child_structural_substitution,
    lower_uk_definition_child_range_substitution,
    lower_uk_metadata_renumber_effect,
    lower_uk_source_carried_inserted_subsection_child_range_substitution,
    lower_uk_source_carried_parent_quoted_child_substitution,
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
from lawvm.uk_legislation.lowering_records import (
    _append_uk_effect_lowering_observation,
    _append_uk_effect_lowering_rejection,
)
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
    _source_carried_inserted_subsection_child_range_substitution,
    _source_carried_parent_quoted_child_substitution,
    _source_carried_structured_tail_substitution,
)
from lawvm.uk_legislation.source_definition_fragments import (
    source_definition_child_range_substitution,
)
from lawvm.uk_legislation.source_definition_structural_insert import (
    source_definition_child_structural_substitution,
    source_definition_child_structural_sibling_insert,
)
from lawvm.uk_legislation.substitution_metadata import (
    UKSourceLabelChangingSubstitution,
    _source_replaced_sibling_count_from_substitution_text,
)
from lawvm.uk_legislation.target_parser import _parse_affected_target, _split_metadata_provisions
from lawvm.uk_legislation.witness_builders import (
    _uk_effect_witness,
    _uk_extraction_witness,
)
from lawvm.uk_legislation.xml_helpers import _text_content
from lawvm.uk_legislation.effect_target_prelude import canonicalize_uk_address
from lawvm.uk_legislation.table_sources import (
    _uk_table_driven_fee_target_refinements,
    address_to_citation,
)
from lawvm.core.quirks_disposition import QuirksDisposition
from lawvm.uk_legislation.strict_profile import active_uk_strict_profile


_UK_EFFECT_FEE_TARGET_REFINEMENT_FAILED_RULE_ID = "uk_effect_fee_target_refinement_failed"

UK_EFFECT_SAVINGS_REFERENCES_QUALIFIED_REPEAL_BLOCKED_RULE_ID = (
    "uk_effect_savings_references_qualified_repeal_blocked"
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


def _trailing_repeal_collides_with_replacement(
    trailing_repeal_refs: list[str],
    replacement_leaf_override: Optional[str],
    label_changing_substitutions: tuple[UKSourceLabelChangingSubstitution, ...],
) -> bool:
    """Return True when a trailing repeal target label equals a new payload label.

    When a label-changing substitution replaces old subsection 11 with a new
    subsection 12, and the trailing repeal also targets subsection 12, the repeal
    must run before the replace so the old subsection 12 is removed before the
    new subsection 12 is created.  Otherwise replay resolves the repeal to the
    newly inserted node and leaves the duplicate old node in place.
    """
    from lawvm.uk_legislation.uk_grafter import _clean_num

    replacement_labels: set[str] = set()
    if replacement_leaf_override:
        replacement_labels.add(_clean_num(replacement_leaf_override))
    for substitution in label_changing_substitutions:
        replacement_labels.add(
            _clean_num(_addr_leaf_label(substitution.replacement_target) or "")
        )
    if not replacement_labels:
        return False
    for ref in trailing_repeal_refs:
        try:
            target = _parse_affected_target(ref)
        except ValueError:
            continue
        repeal_label = _clean_num(_addr_leaf_label(target) or "")
        if repeal_label and repeal_label in replacement_labels:
            return True
    return False


def _savings_qualified_structural_mutation_blocks_lowering(
    effect: UKEffectRecord,
    action: str,
    *,
    extracted_el: Optional[ET._Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> bool:
    """Block lowering when a whole-target repeal is saved by an explicit schedule.

    A UK effect feed entry that names ``ukm:Savings`` provisions is legally
    qualified.  When the savings reference points at a schedule of the
    affecting instrument (e.g. ``schedule-32``), the repeal is being held in
    abeyance for everything covered by that savings schedule.  Until LawVM
    resolves the exact savings scope, the safe default is to withhold the
    deletion rather than remove the target.

    Partial omissions and substitutions with savings references are not blocked
    here; they are individually owned by text-patch lowering paths.  Savings
    references that point at sections, regulations, or other non-schedule
    provisions are treated as ordinary savings clauses and the repeal is left
    to proceed.

    STRICT-PROFILE GATE (Tier C PR2):
    When ``LAWVM_UK_STRICT_PROFILE`` env var is set (the active strict-profile
    carrier is loaded) AND ``allows_uk_savings_qualified_repeal=True``, the
    default-blocking shape is CONDITIONALLY LIFTED — the strict-profile
    explicitly authorizes the repeal to proceed past the savings-qualification
    gate.
    The lift is AUDITED: a non-blocking ``uk_strict_profile_lifted_savings_
    qualified_repeal`` observation is appended alongside any blocking-rejection
    receipt that would have fired under the pre-strict gate. This preserves
    the §0 evidence ledger — the audit-event records WHO authorized the lift
    + why — the called-out carrier identity + the affected savings-reference
    + the recovery-pattern tag — not ad-hoc silent folklore.

    The combination gate (``profile is None or not allows_X: block``)
    mirrors the inverse-of-FI pattern: UK's default IS block; strict
    profile provides the explicit lift-gate for the verified-allowed
    case. Strict-not-allowed preserves the block.
    """
    if action != "repeal":
        return False
    if not any(
        str(ref.get("ref") or "").startswith("schedule-")
        for ref in effect.savings_references
    ):
        return False
    # Tier C PR2 strict-profile gate: when the active strict-profile is loaded
    # AND explicitly allows savings-qualified-repeal recovery, LIFT the default
    # blocking shape. When strict-profile is None (default, no env var set)
    # or the gate is False, the existing blocking path runs unchanged.
    uk_strict_profile = active_uk_strict_profile()
    if (
        uk_strict_profile is not None
        and uk_strict_profile.allows_uk_savings_qualified_repeal
    ):
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id="uk_strict_profile_lifted_savings_qualified_repeal",
            family="savings_qualification",
            reason_code="strict_profile_authorized_savings_qualified_repeal",
            reason=(
                "Strict profile loaded with "
                "allows_uk_savings_qualified_repeal=True; the savings-"
                "qualified whole-target repeal lifting the default-"
                "blocking shape is explicitly authorized by the strict "
                "profile carrier — NOT silently bypassed. The audit-event "
                "records the authorization path so the §0 evidence ledger "
                "remains readable."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "strict_profile_name": uk_strict_profile.core_profile.name,
                "savings_references": effect.savings_references,
                "lowering_action": action,
                "lifted_rejection_rule_id": (
                    UK_EFFECT_SAVINGS_REFERENCES_QUALIFIED_REPEAL_BLOCKED_RULE_ID
                ),
                "strict_disposition": "proceed",
                "quirks_disposition": QuirksDisposition.APPLY,
            },
        )
        return False  # Lift the block — let the repeal proceed to lowering.
    _append_uk_effect_lowering_rejection(
        lowering_rejections_out,
        rule_id=UK_EFFECT_SAVINGS_REFERENCES_QUALIFIED_REPEAL_BLOCKED_RULE_ID,
        # §2.1 named family ``savings_qualification`` — specialises the prior
        # generic ``applicability`` tag so audit/projection consumers can route
        # the savings-qualified repeal residue distinctly. No other UK
        # lowering-rule emit site reads ``applicability`` as a family tag
        # (verified), so the rename is clean and domain-scoped.
        family="savings_qualification",
        reason_code="savings_references_qualify_structural_mutation",
        reason=(
            "UK effect carries a savings reference to an explicit schedule of the "
            "affecting instrument; the whole-target repeal is legally qualified and "
            "is blocked from replay until the savings scope is resolved."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            "savings_references": effect.savings_references,
            "lowering_action": action,
            # §0 over-retention-safe direction: the strict profile MUST block
            # the structural repeal from replay until a claim owns the savings
            # scope; quirks skips the op (no replay mutation) so the saved
            # target survives — the safe wrong vs the forbidden over-repeal.
            # Pinned by tests/test_uk_effect_savings_references.py::
            # test_savings_qualified_repeal_carries_strict_block_quirks_skip_disposition
            # per AGENTS.md §2.9 liveness.
            "strict_disposition": "block",
            "quirks_disposition": QuirksDisposition.SKIP,
        },
    )
    return True


def _prepare_effect_target_prelude(
    *,
    effect: UKEffectRecord,
    effect_type: str,
    action: str,
    extracted_el: Optional[ET._Element],
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


def _withhold_repeal_table_replacement_ops(
    ops: list[LegalOperation],
    *,
    effect: UKEffectRecord,
    effect_type: str,
    extracted_el: Optional[ET._Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> list[LegalOperation]:
    """Drop whole-node structural replaces whose source is a repeal schedule table.

    When the extracted source is a repeal Schedule (a list of repeals by extent,
    not replacement content), a deterministic word-level repeal lowers to a
    ``text_repeal`` (e.g. the quoted ``the words "23,"``) and is kept. But when the
    same source lowers to a whole-node ``replace`` carrying the table itself as the
    payload, applying it would overwrite the target with the repeals table — so the
    op is withheld and the target preserved (over-retention is the safe wrong).
    """
    if not ops or extracted_el is None:
        return ops
    structural_replaces = [
        op
        for op in ops
        if op.action is StructuralAction.REPLACE and op.payload is not None and op.text_patch is None
    ]
    if not structural_replaces:
        return ops
    from lawvm.uk_legislation.source_adjudication import _looks_like_repeal_schedule_table_source

    tag = extracted_el.tag
    extracted_tag = ET.QName(tag).localname if isinstance(tag, str) else None
    if not _looks_like_repeal_schedule_table_source(
        extracted_tag=extracted_tag, effect_type=effect_type, text=extracted_text or ""
    ):
        return ops
    withheld = set(id(op) for op in structural_replaces)
    _append_uk_effect_lowering_rejection(
        lowering_rejections_out,
        rule_id="uk_effect_repeal_table_replacement_payload_rejected",
        family="source_pathology",
        reason_code="repeal_table_payload_not_replacement",
        reason=(
            "A repeal-family effect lowered to a whole-node replace whose payload is "
            "a repeal schedule table (a list of repeals by extent), not genuine "
            "replacement content. Replacing the target with that table would destroy "
            "legal state, so the operation is withheld and the target preserved "
            "(over-retention is the safe wrong)."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            "effect_type_normalized": effect_type,
            "withheld_op_count": len(withheld),
            # §1.8 receipt totality: name the withheld ops, not just count them.
            # The filter's return list omits them; consumers must be able to inspect
            # the rejected lane (op_id, action, target) without re-running lowering.
            "withheld_ops": tuple(
                {
                    "op_id": str(op.op_id or ""),
                    "action": _action_name(op.action),
                    "target": str(op.target or ""),
                }
                for op in structural_replaces
            ),
        },
    )
    return [op for op in ops if id(op) not in withheld]


def _canonicalize_node_eid_letter_case(node: Any) -> tuple[Any, bool]:
    """Return ``(node, changed)`` with canonical-cased eId/id on the subtree."""
    new_children: list[Any] = []
    children_changed = False
    for child in getattr(node, "children", None) or ():
        rebuilt_child, child_changed = _canonicalize_node_eid_letter_case(child)
        new_children.append(rebuilt_child)
        children_changed = children_changed or child_changed
    new_attrs: Optional[dict[str, Any]] = None
    attrs = getattr(node, "attrs", None) or {}
    for key in ("eId", "id"):
        value = attrs.get(key)
        if isinstance(value, str) and value:
            canonical = _uk_canonicalize_eid_letter_case(value)
            if canonical != value:
                if new_attrs is None:
                    new_attrs = dict(attrs)
                new_attrs[key] = canonical
    if new_attrs is None and not children_changed:
        return node, False
    replacements: dict[str, Any] = {}
    if new_attrs is not None:
        replacements["attrs"] = new_attrs
    if children_changed:
        replacements["children"] = new_children
    return dc_replace(node, **replacements), True


def _canonicalize_payload_eid_letter_case(ops: list[LegalOperation]) -> list[LegalOperation]:
    """Uppercase inserted-provision letter suffixes on emitted payload eIds.

    UK eId convention writes the letter portion of a provision number in upper
    case (``section-20A``, ``section-24-3A``, ``section-23ZA``).  The lowering
    pipeline derives payload eIds from labels that were lower-cased during target
    parsing, so a synthesized inserted provision carries ``section-20a``.  That
    lower-cased eId is *not* in the oracle's id set, so grounding clears it and
    re-matches it by fuzzy text (a non-deterministic crutch).  Canonicalizing the
    letter case here makes the synthesized eId equal to the oracle's structural
    eId, so grounding preserves it exactly (direction (b), OPC §6.4).

    ``IRNode``/``LegalOperation`` are frozen, so changed payloads are rebuilt via
    ``dataclasses.replace``.  Only emitted payload eId attributes are touched;
    matching keys are constructed elsewhere and stay lower-cased.
    """
    rebuilt: list[LegalOperation] = []
    for op in ops:
        payload = getattr(op, "payload", None)
        if payload is None:
            rebuilt.append(op)
            continue
        new_payload, changed = _canonicalize_node_eid_letter_case(payload)
        rebuilt.append(dc_replace(op, payload=new_payload) if changed else op)
    return rebuilt


def compile_effect_to_ir_ops(
    effect: UKEffectRecord,
    extracted_el: Optional[ET._Element],
    sequence: int = 0,
    fallback_for_missing_extracted_source: bool = False,
    lowering_rejections_out: Optional[list[dict[str, Any]]] = None,
    allow_payload_identity_synthesis: bool = True,
    source_root: Optional[ET._Element] = None,
    source_authority_layer: str = "",
    lower_phase_timings_out: Optional[dict[str, float]] = None,
) -> list[LegalOperation]:
    """Compile a UKEffectRecord + XML element into LawVM LegalOperations.

    Word-level effects lower to typed text-patch operations. Structural effects
    lower to canonical replace/repeal/insert operations only when source and
    target evidence support that action family.
    """
    ops = _compile_effect_to_ir_ops_impl(
        effect,
        extracted_el,
        sequence=sequence,
        fallback_for_missing_extracted_source=fallback_for_missing_extracted_source,
        lowering_rejections_out=lowering_rejections_out,
        allow_payload_identity_synthesis=allow_payload_identity_synthesis,
        source_root=source_root,
        source_authority_layer=source_authority_layer,
        lower_phase_timings_out=lower_phase_timings_out,
    )
    ops = _withhold_repeal_table_replacement_ops(
        ops,
        effect=effect,
        effect_type=(effect.effect_type or "").strip().lower(),
        extracted_el=extracted_el,
        extracted_text=_text_content(extracted_el) if extracted_el is not None else None,
        lowering_rejections_out=lowering_rejections_out,
    )
    return _canonicalize_payload_eid_letter_case(ops)


def _compile_effect_to_ir_ops_impl(
    effect: UKEffectRecord,
    extracted_el: Optional[ET._Element],
    sequence: int = 0,
    fallback_for_missing_extracted_source: bool = False,
    lowering_rejections_out: Optional[list[dict[str, Any]]] = None,
    allow_payload_identity_synthesis: bool = True,
    source_root: Optional[ET._Element] = None,
    source_authority_layer: str = "",
    lower_phase_timings_out: Optional[dict[str, float]] = None,
) -> list[LegalOperation]:
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
        # §1.8 (replay conservation): every filtered/rejected/skipped op MUST
        # be visible with a receipt. ``_COMMENCEMENT_EFFECT_TYPES`` carries the
        # explicit commencement effect-feed type tags (``appointed day(s)``,
        # ``coming into force``, ``commencement order``); structural replay
        # cannot synthesise a mutation from commencement language — replay
        # returns zero ops. That alone is the §0 over-retention-safe skip.
        # WITHOUT this receipt the downstream manual-frontier classifier at
        # ``source_adjudication.py:2303`` (``uk_manual_frontier_
        # commencement_effect_out_of_scope`` routing on
        # ``uk_effect_commencement_source_rejected``) WOULD see ``lowering_
        # rules`` as empty for this lane — the Path-2 emit at
        # ``source_action_inference.py:241`` handles the empty-type-text-
        # commencement case but NOT this explicit-type path. This emission
        # closes the §1.8 silent-drop (per audit agent EV-1 in
        # notes_internal: ``return []`` without receipt flagged as a true
        # silent-drop) and routs the row downstream to its manual-frontier
        # classification cleanly.
        _append_uk_effect_lowering_rejection(
            lowering_rejections_out,
            rule_id="uk_effect_commencement_source_rejected",
            family="applicability_scope",
            reason_code="commencement_effect_type_out_of_scope",
            reason=(
                "UK effect carries an explicit commencement-feed effect type; "
                "structural replay cannot synthesise a mutation from in-force / "
                "commencement language. The effect is routed downstream as a "
                "manual-frontier commencement-effect-out-of-scope row (not a "
                "replay mutation) so the row is owned rather than silently "
                "dropped."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "effect_type_normalized": effect_type,
                "affected_provisions": effect.affected_provisions,
                "lowering_action": None,
                "strict_disposition": "block",
                "quirks_disposition": "skip",
                # AGENTS.md §0 promotion-chain: the lowering receipt records
                # the boundary; the manual-frontier classification downstream
                # is NOT a replay authority — the row remains out-of-scope
                # until a future temporal/applicability model owns it.
                "manual_frontier_axis": "commencement_effect_type",
            },
        )
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

    if _savings_qualified_structural_mutation_blocks_lowering(
        effect,
        action,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        lowering_rejections_out=lowering_rejections_out,
    ):
        _mark_lower_phase("compile_lower_savings_guard")
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

    definition_child_structural_substitution = source_definition_child_structural_substitution(
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        affected_provisions=effect.affected_provisions,
    )
    if action == "replace" and definition_child_structural_substitution is not None:
        ops = lower_uk_definition_child_structural_substitution(
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            sequence=sequence,
            definition_child_substitution=definition_child_structural_substitution,
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
            _uk_sp = active_uk_strict_profile()
            if (
                _uk_sp is not None
                and _uk_sp.allows_uk_definition_child_structural_insert
            ):
                _append_uk_effect_lowering_observation(
                    lowering_rejections_out,
                    rule_id="uk_strict_profile_lifted_definition_child_structural_insert",
                    family="definition_entry_elaboration",
                    reason_code="strict_profile_authorized_definition_child_structural_insert",
                    reason=(
                        "Strict profile loaded with "
                        "allows_uk_definition_child_structural_insert=True; "
                        "the blocking definition-child structural insert is "
                        "explicitly authorized to proceed past the blocking "
                        "flag — lowering will attempt the child insert."
                    ),
                    effect=effect,
                    extracted_el=extracted_el,
                    extracted_text=extracted_text,
                    detail={
                        "strict_profile_name": _uk_sp.core_profile.name,
                        "lifted_blocking_rule_id": str(
                            definition_child_structural_insert.get("rule_id", "")
                        ),
                        "strict_disposition": "proceed",
                        "quirks_disposition": QuirksDisposition.APPLY,
                    },
                )
                # Fall through to the lower_uk_definition_child_structural_
                # sibling_insert call below — don't return [].
            else:
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
        source_root=source_root,
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
    parent_quoted_child_substitution = _source_carried_parent_quoted_child_substitution(
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        affected_provisions=effect.affected_provisions,
    )
    if action in {"replace", "text_replace"} and parent_quoted_child_substitution is not None:
        ops = lower_uk_source_carried_parent_quoted_child_substitution(
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            sequence=sequence,
            parent_child_substitution=parent_quoted_child_substitution,
            effect_witness=effect_witness,
            extraction_witness=extraction_witness,
            lowering_rejections_out=lowering_rejections_out,
        )
        _mark_lower_phase("compile_lower_special")
        return ops
    inserted_subsection_child_range_substitution = (
        _source_carried_inserted_subsection_child_range_substitution(
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            affected_provisions=effect.affected_provisions,
        )
    )
    if (
        action in {"replace", "text_replace"}
        and inserted_subsection_child_range_substitution is not None
    ):
        ops = lower_uk_source_carried_inserted_subsection_child_range_substitution(
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            sequence=sequence,
            inserted_subsection_child_range_substitution=(
                inserted_subsection_child_range_substitution
            ),
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
        _fee_refinement_failed_helper: str = ""
        _fee_refinement_exc: ValueError | None = None
        try:
            _fee_refinement_failed_helper = "parse_affected_target"
            parsed_target = _parse_affected_target(t_str)
            _fee_refinement_failed_helper = "canonicalize"
            target = canonicalize_uk_address(parsed_target)
            _fee_refinement_failed_helper = "table_driven_refinement"
            refinement_addresses = _uk_table_driven_fee_target_refinements(
                effect=effect,
                source_root=source_root,
                target=target,
            )
            _fee_refinement_failed_helper = ""
            if refinement_addresses:
                for ref_target in refinement_addresses:
                    refined_targets_str.append(address_to_citation(ref_target))
            else:
                refined_targets_str.append(t_str)
        except ValueError as exc:
            _fee_refinement_exc = exc
            refined_targets_str.append(t_str)
        if _fee_refinement_exc is not None:
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id=_UK_EFFECT_FEE_TARGET_REFINEMENT_FAILED_RULE_ID,
                family="lowering_rejection",
                reason_code="fee_target_refinement_failed",
                reason=(
                    f"fee-target refinement failed in {_fee_refinement_failed_helper}: "
                    f"{_fee_refinement_exc}"
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    "input_t_str": t_str,
                    "failed_helper": _fee_refinement_failed_helper,
                    "exc_message": str(_fee_refinement_exc),
                    "strict_disposition": "block",
                    "quirks_disposition": QuirksDisposition.APPLY,
                },
            )
    targets_str = refined_targets_str
    original_targets_str = list(targets_str)
    mixed_heading_source_ref_by_target = target_prelude.mixed_heading_source_ref_by_target
    trailing_repeal_refs = target_prelude.trailing_repeal_refs
    replacement_leaf_override = target_prelude.replacement_leaf_override
    replacement_leaf_kind = target_prelude.replacement_leaf_kind
    label_changing_substitutions = target_prelude.label_changing_substitutions

    # Some UK source extractions resolve to a bare payload fragment (e.g. one
    # enumerated item of a multi-item repeal/insert list) while the parent
    # amendment container supplies the missing instruction verb.  When we can
    # safely reconstruct a complete instruction, augment extracted_text before
    # shape classification and fragment parsing so the operation is not gated
    # purely because the verb lives in the ancestor.
    if action in {"replace", "text_replace"}:
        augmented_extracted_text = augment_extracted_text_with_instruction_context(
            extracted_text=extracted_text,
            extracted_el=extracted_el,
            source_root=source_root,
        )
        lowering_extracted_text = augmented_extracted_text or extracted_text
    else:
        lowering_extracted_text = extracted_text

    if (
        action in {"replace", "text_replace"}
        and is_word_level
        and source_shape_blocks_before_text_patch_lowering(
            lowering_extracted_text,
            original_targets_str,
        )
    ):
        append_unlowered_overlap_substitution_rejection(
            lowering_rejections_out,
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            effect_type=effect_type,
            original_targets_str=original_targets_str,
            target_candidate_count=len(targets_str),
            unlowered_overlap_substitution_targets=original_targets_str,
            unlowered_overlap_substitution_reason="source_shape_pre_target_block",
            source_root=source_root,
        )
        _mark_lower_phase("compile_lower_target_setup")
        return []

    ops = []
    unlowered_overlap_substitution_targets: list[str] = []
    unlowered_overlap_substitution_reason = ""
    chained_insert_anchor = _ChainedInsertAnchorState()
    structured_crossheading_op_built = False
    if action == "insert":
        crossheading_insert_ops = build_crossheading_insert_ops(
            effect=effect,
            extracted_el=extracted_el,
            sequence=sequence,
            effect_witness=effect_witness,
            extraction_witness=extraction_witness,
        )
        structured_crossheading_op_built = bool(crossheading_insert_ops)
        ops.extend(crossheading_insert_ops)
    source_replaced_sibling_count = (
        _source_replaced_sibling_count_from_substitution_text(
            extracted_text=lowering_extracted_text,
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
                lowering_extracted_text=lowering_extracted_text,
                source_root=source_root,
                chained_insert_anchor=chained_insert_anchor,
                lowering_rejections_out=lowering_rejections_out,
                target_index=target_index,
                structured_crossheading_op_built=structured_crossheading_op_built,
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
        trailing_repeal_ops = build_trailing_repeal_ops(
            effect=effect,
            sequence=sequence,
            trailing_repeal_refs=trailing_repeal_refs,
            effect_witness=effect_witness,
            extraction_witness=extraction_witness,
            original_targets_str=original_targets_str,
            source_parent_substitution_range_payload=source_parent_substitution_range_payload,
        )
        if _trailing_repeal_collides_with_replacement(
            trailing_repeal_refs,
            replacement_leaf_override,
            label_changing_substitutions,
        ):
            ops = trailing_repeal_ops + ops
        else:
            ops.extend(trailing_repeal_ops)
    _mark_lower_phase("compile_lower_tail")
    return ops
