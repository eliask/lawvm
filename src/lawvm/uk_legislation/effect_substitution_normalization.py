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
from lawvm.uk_legislation.target_anchors import (
    _fallback_target_eid,
)
from lawvm.uk_legislation.target_parser import _parse_affected_target
from lawvm.uk_legislation.uk_grafter import _clean_num


UK_EFFECT_AFTER_ANCHOR_INSERT_PROMOTED_RULE_ID = (
    "uk_effect_after_anchor_insert_promoted"
)

UK_EFFECT_BLOCK_SUBSTITUTION_TAIL_PROMOTED_RULE_ID = (
    "uk_effect_block_substitution_tail_promoted_to_insert_after"
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


def _block_substitution_tail_insert_detail(
    *,
    original_target_refs: list[str],
    target_index: int,
    target: LegalAddress,
    content_ir: Optional[dict[str, Any]],
    source_payload_actual_el: Optional[ET.Element],
) -> Optional[dict[str, Any]]:
    """Return promotion detail when this op is a block-substitution letter-suffix tail.

    Pattern B (Sensor K): an effect whose affected_provisions is a range like
    's. 25(4)-(4B)' decomposes into ops _0/_1/_2 targeting the numeric stem and its
    letter-suffix variants.  Op _0 is a legitimate Replace on the existing numeric
    stem (e.g. subsection:4).  Ops _1..._n target letter-suffix variants (4a, 4b)
    that do not yet exist; emitting them as Replace forces replay-time recovery
    (uk_replay_replace_materialized_as_insert_for_missing_leaf).  The correct
    lowering is InsertAfter(anchor=<previous target in group>).

    Guards:
    1. source_payload_actual_el is non-None (real XML, not synthesised)
    2. target_index > 0 and group has >= 2 members
    3. _source_payload_matches_target_leaf holds for this tail op
    4. Current leaf label is a letter-suffix (digits + letters, e.g. 4a)
    5. Group stem (index 0) has the plain numeric stem (e.g. 4) as its leaf label
    6. Parent paths are consistent across the group
    """
    if source_payload_actual_el is None:
        return None
    if target_index <= 0 or len(original_target_refs) < 2:
        return None
    if not _source_payload_matches_target_leaf(content_ir, target):
        return None
    # Current leaf must have a letter-suffix label (digits followed by letters).
    leaf_label = _clean_num(_addr_leaf_label(target) or "")
    stem = _letter_suffix_anchor_label(leaf_label)
    if stem is None:
        return None
    # Group's index-0 target must have the plain numeric stem as its leaf label.
    try:
        stem_target = _parse_affected_target(original_target_refs[0])
    except ValueError:
        return None
    stem_leaf_label = _clean_num(_addr_leaf_label(stem_target) or "")
    if stem_leaf_label != stem:
        return None
    # Parent path of stem target must match current target's parent path.
    if tuple(stem_target.path[:-1]) != tuple(target.path[:-1]):
        return None
    # Previous target in group is the anchor for InsertAfter.
    try:
        prev_target = _parse_affected_target(original_target_refs[target_index - 1])
    except ValueError:
        return None
    if tuple(prev_target.path[:-1]) != tuple(target.path[:-1]):
        return None
    anchor_eid = _fallback_target_eid(prev_target)
    leaf_kind = _addr_leaf_kind(target) or ""
    payload_label = str(content_ir.get("label") or "") if content_ir else ""
    payload_kind = str(content_ir.get("kind") or "") if content_ir else ""
    return {
        "stem_ref": original_target_refs[0],
        "stem_leaf_label": stem_leaf_label,
        "target_ref": original_target_refs[target_index],
        "target": str(target),
        "target_index": str(target_index),
        "prev_ref": original_target_refs[target_index - 1],
        "leaf_kind": leaf_kind,
        "leaf_label": leaf_label,
        "payload_kind": payload_kind,
        "payload_label": payload_label,
        "anchor_eid": anchor_eid,
        "strict_disposition": "apply",
        "quirks_disposition": "apply",
    }


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

    # --- Pattern B: block-substitution group letter-suffix tail promotion ---
    # When an effect decomposes into a multi-target group (e.g. s.25(4)-(4B) →
    # ops _0/_1/_2) the tail ops target letter-suffix labels (4a, 4b) that don't
    # yet exist.  Detect by: (a) group membership (target_index > 0, group[0] is
    # the plain numeric stem), (b) current leaf is a letter-suffix of that stem,
    # (c) payload matches target leaf.  Anchor: the immediately preceding target
    # in the group, enabling correct InsertAfter chaining (4a after 4, 4b after 4a).
    if curr_action == "replace":
        block_sub_tail_detail = _block_substitution_tail_insert_detail(
            original_target_refs=original_target_refs,
            target_index=target_index,
            target=target,
            content_ir=content_ir,
            source_payload_actual_el=source_payload_actual_el,
        )
        if block_sub_tail_detail is not None:
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id=UK_EFFECT_BLOCK_SUBSTITUTION_TAIL_PROMOTED_RULE_ID,
                family="targeted_after_anchor_insert",
                reason_code="block_substitution_letter_suffix_tail_promoted_to_insert",
                reason=(
                    "UK block-substitution group decomposes into a numeric-stem "
                    "Replace op and one or more letter-suffix tail ops (e.g. 4a, 4b "
                    "after 4).  The tail targets do not yet exist in the materialized "
                    "state; lowering promotes each tail from Replace to InsertAfter "
                    "with the preceding sibling in the group as anchor, replacing "
                    "replay-time uk_replay_replace_materialized_as_insert_for_missing_leaf "
                    "recovery with deterministic lowering."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail=block_sub_tail_detail,
            )
            anchor_eid = str(block_sub_tail_detail["anchor_eid"])
            return UKSubstitutedPayloadInsertNormalization(
                curr_action="insert",
                anchor_preceding_eid=anchor_eid,
                anchor_preceding_eid_source=UK_EFFECT_BLOCK_SUBSTITUTION_TAIL_PROMOTED_RULE_ID,
            )

    # --- Letter-suffix new-leaf insert promotion (A13 widened for Pattern C) ---
    # When a Replace op targets a provision with an alphanumeric-suffix label
    # (e.g. section:19/subsection:3a, section:1a) and the payload matches the
    # target leaf exactly, the source is inserting a genuinely new provision
    # that did not exist before. UK drafting uses "3A" to mean "inserted between
    # 3 and 4". Emitting Replace here relies on replay-time recovery
    # (uk_replay_replace_materialized_as_insert_for_missing_leaf). Promoting to
    # Insert with the numeric-stem anchor is the correct lowering shape.
    #
    # Guard 4 (original A13): instruction text contained "after [anchor] insert".
    # This was too defensive — Pattern C cases are pure substitutions of a
    # letter-suffix leaf ("For subsection (1A) substitute—") whose target doesn't
    # yet exist in the replayed state because an earlier insertion wasn't replayed.
    # The instruction-text guard excluded them. Widened guard: target leaf label
    # is structurally a letter-suffix (digits + letters, e.g. 3A, 1B, 6ZA). This
    # is the same structural signal A13 already uses via _letter_suffix_anchor_address.
    # Guards 1–3 (real XML payload, payload matches target leaf) are unchanged and
    # remain the primary false-positive filter: genuine in-place edits on existing
    # letter-suffix leaves normally carry a text-fragment payload that does NOT
    # structurally match the leaf, so _source_payload_matches_target_leaf rejects them.
    #
    # Pattern B (block-substitution group tail) is handled above by A15 and cannot
    # reach here (A15 guard requires target_index > 0 within a group of >= 2).
    if (
        curr_action == "replace"
        and source_payload_actual_el is not None
        and _source_payload_matches_target_leaf(content_ir, target)
    ):
        anchor_addr = _letter_suffix_anchor_address(target)
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
