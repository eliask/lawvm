"""Substitution-series normalization for UK effect lowering."""

from __future__ import annotations

import re
from dataclasses import dataclass
import xml.etree.ElementTree as ET
from typing import Any, Optional

from lawvm.core.ir import LegalAddress
from lawvm.uk_legislation.addressing import _addr_leaf_kind, _addr_leaf_label
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.lowering_records import _append_uk_effect_lowering_observation
from lawvm.uk_legislation.source_payload_elaboration import (
    _source_payload_matches_target_leaf,
    _substituted_series_new_sibling_insert_detail,
    _substituted_series_pre_anchor_sibling_insert_detail,
)
from lawvm.uk_legislation.provision_extractor import (
    _instruction_text_before_amendment_container,
)
from lawvm.uk_legislation.target_anchors import (
    _fallback_target_eid,
    _source_after_insertion_anchor,
)
from lawvm.uk_legislation.uk_grafter import _clean_num


UK_EFFECT_AFTER_ANCHOR_INSERT_PROMOTED_RULE_ID = (
    "uk_effect_after_anchor_insert_promoted"
)

# Alphanumeric-suffix label: numeric stem followed by one or more letters.
# Examples: 3A, 1B, 6ZA, 1ZA, 11ZF.
_LETTER_SUFFIX_LABEL_RE = re.compile(r"^(\d+)([A-Za-z]+)$")


def _letter_suffix_anchor_label(label: str) -> Optional[str]:
    """Return the numeric-stem anchor label for a letter-suffix UK provision label.

    For label '3A' returns '3'; for '6ZA' returns '6'; for '11ZF' returns '11'.
    Returns None for plain numeric labels, pure-alpha labels, or any label that
    does not match the <digits><letters> pattern.

    This is the canonical signal that a provision is a new insert between existing
    numbered siblings, not a replacement of an existing one.
    """
    clean = _clean_num(label or "")
    m = _LETTER_SUFFIX_LABEL_RE.match(clean)
    if m is None:
        return None
    return m.group(1)


def _letter_suffix_anchor_address(target: LegalAddress) -> Optional[LegalAddress]:
    """Return the anchor address (preceding sibling's numeric stem) for a letter-suffix target.

    For target section:19/subsection:3a, returns section:19/subsection:3.
    For target section:1a, returns a single-element address section:1.
    Returns None if the leaf label is not a letter-suffix label.
    """
    if not target.path:
        return None
    leaf_kind, leaf_label = target.path[-1]
    anchor_label = _letter_suffix_anchor_label(leaf_label)
    if not anchor_label:
        return None
    return LegalAddress(path=(*target.path[:-1], (leaf_kind, anchor_label)))


@dataclass(frozen=True)
class UKSubstitutedPayloadInsertNormalization:
    curr_action: str
    anchor_preceding_eid: Optional[str] = None
    anchor_preceding_eid_source: str = "effect_comments_after_clause"


def lower_substituted_payload_insert_normalization(
    *,
    effect: UKEffectRecord,
    curr_action: str,
    original_target_refs: list[str],
    target_index: int,
    target_ref: str,
    target: LegalAddress,
    content_ir: Optional[dict[str, Any]],
    source_replaced_sibling_count: Optional[int],
    source_payload_actual_el: Optional[ET.Element],
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> UKSubstitutedPayloadInsertNormalization:
    substituted_series_insert_detail = _substituted_series_new_sibling_insert_detail(
        effect_type=effect.effect_type,
        original_target_refs=original_target_refs,
        target_index=target_index,
        target_ref=target_ref,
        target=target,
        content_ir=content_ir,
    )
    if substituted_series_insert_detail is not None:
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id="uk_effect_substituted_series_new_sibling_insert_lowered",
            family="lowering_normalization",
            reason_code="substituted_for_single_old_target_with_new_sibling_payload",
            reason=(
                "UK substituted-for row names one replaced target but the "
                "source-backed replacement series contains an additional "
                "sibling payload; lowering preserves the first target as "
                "replace and lowers later source-owned siblings as inserts"
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail=substituted_series_insert_detail,
        )
        return UKSubstitutedPayloadInsertNormalization(curr_action="insert")

    substituted_series_pre_anchor_insert_detail = _substituted_series_pre_anchor_sibling_insert_detail(
        effect_type=effect.effect_type,
        original_target_refs=original_target_refs,
        target_index=target_index,
        target_ref=target_ref,
        target=target,
        content_ir=content_ir,
    )
    if substituted_series_pre_anchor_insert_detail is not None:
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id="uk_effect_substituted_series_pre_anchor_sibling_insert_lowered",
            family="lowering_normalization",
            reason_code="substituted_for_single_old_target_with_pre_anchor_sibling_payload",
            reason=(
                "UK substituted-for row names one replaced target but the "
                "source-backed replacement series contains an additional "
                "sibling payload before that anchor; lowering preserves the "
                "named anchor as replace and lowers earlier source-owned "
                "siblings as inserts."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail=substituted_series_pre_anchor_insert_detail,
        )
        return UKSubstitutedPayloadInsertNormalization(curr_action="insert")

    if (
        source_replaced_sibling_count is not None
        and target_index >= source_replaced_sibling_count
        and _source_payload_matches_target_leaf(content_ir, target)
    ):
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id="uk_effect_substituted_range_extra_payload_sibling_insert_lowered",
            family="lowering_normalization",
            reason_code="source_substitution_payload_contains_extra_sibling",
            reason=(
                "UK source substitutes a bounded sibling range but the "
                "BlockAmendment contains additional source-owned sibling "
                "payloads beyond the replaced range; lowering keeps the "
                "range members as replacements and lowers the extra "
                "siblings as inserts."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": target_ref,
                "target": str(target),
                "replaced_sibling_count": source_replaced_sibling_count,
                "source_payload_kind": str(content_ir.get("kind") or "") if content_ir else "",
                "source_payload_label": str(content_ir.get("label") or "") if content_ir else "",
            },
        )
        return UKSubstitutedPayloadInsertNormalization(curr_action="insert")

    # --- Letter-suffix new-leaf insert promotion ---
    # When a Replace op targets a provision with an alphanumeric-suffix label
    # (e.g. section:19/subsection:3a, section:1a) and the payload matches the
    # target leaf exactly, the source is inserting a genuinely new provision
    # that did not exist before. UK drafting uses "3A" to mean "inserted between
    # 3 and 4". Emitting Replace here relies on replay-time recovery
    # (uk_replay_replace_materialized_as_insert_for_missing_leaf). Promoting to
    # Insert with the numeric-stem anchor is the correct lowering shape.
    # Guard: only promote when the payload came from real source XML (not
    # synthesized by infer_source_payload_from_target) AND the instruction text
    # confirms an "after [anchor] insert" pattern. Without the second guard,
    # any "substituted" effect targeting an existing letter-suffix provision
    # (e.g. "For subsection (1A) substitute—") would be incorrectly promoted.
    if (
        curr_action == "replace"
        and source_payload_actual_el is not None
        and _source_payload_matches_target_leaf(content_ir, target)
    ):
        instruction_text = (
            _instruction_text_before_amendment_container(extracted_el)
            if extracted_el is not None
            else (extracted_text or "")
        )
        source_anchor = _source_after_insertion_anchor(instruction_text, target)
        anchor_addr = (
            _letter_suffix_anchor_address(target) if source_anchor.eid else None
        )
        if anchor_addr is not None:
            anchor_eid = _fallback_target_eid(anchor_addr)
            leaf_kind = _addr_leaf_kind(target) or ""
            leaf_label = _addr_leaf_label(target) or ""
            payload_label = str(content_ir.get("label") or "") if content_ir else ""
            payload_kind = str(content_ir.get("kind") or "") if content_ir else ""
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id=UK_EFFECT_AFTER_ANCHOR_INSERT_PROMOTED_RULE_ID,
                family="targeted_after_anchor_insert",
                reason_code="replace_for_letter_suffix_new_leaf_promoted_to_insert",
                reason=(
                    "UK effect targets a provision with a letter-suffix label "
                    "(e.g. 3A, 1B, 6ZA) whose payload matches the target leaf "
                    "exactly; such labels are definitionally new provisions "
                    "inserted between existing numbered siblings. Lowering "
                    "promotes from Replace to Insert with the numeric-stem "
                    "sibling as anchor instead of relying on replay-time "
                    "uk_replay_replace_materialized_as_insert_for_missing_leaf "
                    "recovery."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    "target_ref": target_ref,
                    "target": str(target),
                    "leaf_kind": leaf_kind,
                    "leaf_label": leaf_label,
                    "payload_kind": payload_kind,
                    "payload_label": payload_label,
                    "anchor_address": str(anchor_addr),
                    "anchor_eid": anchor_eid,
                    "strict_disposition": "apply",
                    "quirks_disposition": "apply",
                },
            )
            return UKSubstitutedPayloadInsertNormalization(
                curr_action="insert",
                anchor_preceding_eid=anchor_eid,
                anchor_preceding_eid_source=UK_EFFECT_AFTER_ANCHOR_INSERT_PROMOTED_RULE_ID,
            )

    return UKSubstitutedPayloadInsertNormalization(curr_action=curr_action)
