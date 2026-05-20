"""Ordering helpers for UK effect replay and text-patch lowering."""
from __future__ import annotations

import re
from typing import Any, Optional, Sequence

from lawvm.core.ir import LegalOperation
from lawvm.core.semantic_types import TextPatchKindEnum
from lawvm.roman import roman_to_arabic as _shared_roman_to_arabic
from lawvm.uk_legislation.addressing import _action_name
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.uk_grafter import _clean_num


def _label_sort_key(label: Optional[str]) -> tuple[Any, ...]:
    """Return a deterministic natural sort key for UK structural labels."""
    clean = _clean_num(label or "")
    if not clean:
        return ((-1, ""),)
    parts = re.findall(r"\d+|[a-z]+", clean)
    key: list[Any] = []
    for part in parts:
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, part))
    return tuple(key)


def _uk_source_provision_label_sort_key(label: str, *, previous_alpha: bool = False) -> tuple[Any, ...]:
    """Return a natural sort key for one label in an affecting-provision citation.

    This is intentionally separate from ``_clean_num`` because parenthesized
    labels such as ``(d)`` are alphabetic legal labels, not Roman numerals.
    """
    token = re.sub(r"[^0-9A-Za-z]+", "", str(label or "")).lower()
    if not token:
        return (9, "")
    match = re.fullmatch(r"(\d+)([a-z]*)", token)
    if match is not None:
        suffix = match.group(2)
        suffix_key = tuple(ord(ch) - ord("a") + 1 for ch in suffix)
        return (0, int(match.group(1)), suffix_key)
    roman_value = _shared_roman_to_arabic(token)
    if roman_value is not None and (len(token) > 1 or previous_alpha):
        return (2, roman_value)
    if token.isalpha():
        return (1, tuple(ord(ch) - ord("a") + 1 for ch in token))
    return (8, token)


def _uk_source_provision_order_key(ref: str) -> tuple[Any, ...]:
    """Return a stable legal-source-order key for an affecting provision ref.

    The effects feed identifiers are opaque hashes; when multiple effects have
    the same effective date and affecting act, source provision order is the
    defensible execution order.
    """
    text = " ".join(str(ref or "").replace("\u00a0", " ").split()).lower()
    token_re = re.compile(
        r"\b(?:regs?|regulations?|rules?|articles?|arts?|sections?|ss?|s|"
        r"schedules?|schs?|sch|paragraphs?|paras?|para)\.?\s*(?P<label>[0-9]+[A-Za-z]*)"
        r"|\((?P<paren>[0-9A-Za-z]+)\)"
    )
    tokens: list[tuple[Any, ...]] = []
    previous_alpha = False
    for match in token_re.finditer(text):
        raw_label = match.group("label") or match.group("paren") or ""
        key = _uk_source_provision_label_sort_key(raw_label, previous_alpha=previous_alpha)
        tokens.append(key)
        previous_alpha = bool(key and key[0] == 1)
    return (tuple(tokens), text)


def _order_uk_effects_for_replay(
    effects: Sequence[UKEffectRecord],
    *,
    diagnostics_out: Optional[list[dict[str, Any]]] = None,
    lowering_observations_out: Optional[list[dict[str, Any]]] = None,
) -> list[UKEffectRecord]:
    """Order UK effects by legal time and affecting-source citation order."""

    original = list(effects)

    def _sort_key(e: UKEffectRecord) -> tuple[Any, ...]:
        return (
            e.effective_date or "9999-99-99",
            str(e.modified or ""),
            e.affecting_act_id,
            _uk_source_provision_order_key(e.affecting_provisions),
            e.effect_id,
        )

    ordered = sorted(original, key=_sort_key)
    if diagnostics_out is None and lowering_observations_out is None:
        return ordered

    groups: dict[tuple[str, str, str], list[UKEffectRecord]] = {}
    for effect in original:
        group_key = (
            effect.effective_date or "9999-99-99",
            str(effect.modified or ""),
            effect.affecting_act_id,
        )
        groups.setdefault(group_key, []).append(effect)

    for group_key, group_effects in groups.items():
        if len(group_effects) < 2:
            continue
        old_ids = [effect.effect_id for effect in group_effects]
        group_object_ids = {id(effect) for effect in group_effects}
        new_group = [effect for effect in ordered if id(effect) in group_object_ids]
        new_ids = [effect.effect_id for effect in new_group]
        if old_ids == new_ids:
            continue
        record = {
            "rule_id": "uk_effect_source_provision_order_normalized",
            "family": "temporal_recovery",
            "phase": "lowering",
            "effective_date": group_key[0],
            "modified": group_key[1],
            "affecting_act_id": group_key[2],
            "reason_code": "same_date_same_affecting_act_source_citation_order",
            "original_effect_ids": tuple(old_ids),
            "ordered_effect_ids": tuple(new_ids),
            "original_affecting_provisions": tuple(effect.affecting_provisions for effect in group_effects),
            "ordered_affecting_provisions": tuple(effect.affecting_provisions for effect in new_group),
            "reason": (
                "UK effects with the same effective date and affecting act "
                "were ordered by source provision citation rather than opaque effect id"
            ),
            "blocking": False,
            "strict_disposition": "record",
            "quirks_disposition": "record",
        }
        if diagnostics_out is not None:
            diagnostics_out.append(record)
        if lowering_observations_out is not None:
            lowering_observations_out.append(dict(record))
    return ordered


def _text_replace_preimage_chain_key(op: LegalOperation) -> Optional[tuple[str, str]]:
    if _action_name(op.action) != "text_replace" or op.text_patch is None:
        return None
    if op.text_patch.kind is not TextPatchKindEnum.REPLACE:
        return None
    if op.text_patch.replacement is None:
        return None
    match_text = op.text_patch.selector.match_text
    replacement = op.text_patch.replacement
    if not match_text or not replacement:
        return None
    if match_text.startswith(("TEXT_", "FROM_")):
        return None
    source = op.source
    return (str(op.target), source.effective if source else "")


def _order_uk_text_patch_preimage_chains(
    ops: Sequence[LegalOperation],
    *,
    lowering_observations_out: Optional[list[dict[str, Any]]] = None,
) -> list[LegalOperation]:
    """Order exact same-target text patches by their quoted preimage chain.

    This is intentionally narrow: only exact `replacement == next.match_text`
    dependencies inside the same target and same effective-date bucket are used.
    No numeric matching, fuzzy matching, or cross-target inference is allowed.
    """
    ordered = list(ops)
    groups: dict[tuple[str, str], list[int]] = {}
    for idx, op in enumerate(ordered):
        key = _text_replace_preimage_chain_key(op)
        if key is not None:
            groups.setdefault(key, []).append(idx)

    for (target, effective_date), indices in groups.items():
        if len(indices) < 2:
            continue
        group_ops = [ordered[idx] for idx in indices]
        successors: dict[int, set[int]] = {i: set() for i in range(len(group_ops))}
        predecessors: dict[int, set[int]] = {i: set() for i in range(len(group_ops))}
        for left_idx, left in enumerate(group_ops):
            if left.text_patch is None:
                continue
            replacement = left.text_patch.replacement or ""
            if not replacement:
                continue
            for right_idx, right in enumerate(group_ops):
                if left_idx == right_idx or right.text_patch is None:
                    continue
                if replacement == right.text_patch.selector.match_text:
                    successors[left_idx].add(right_idx)
                    predecessors[right_idx].add(left_idx)
        if not any(successors.values()):
            continue
        ambiguous = any(len(items) > 1 for items in successors.values()) or any(
            len(items) > 1 for items in predecessors.values()
        )
        if ambiguous:
            if lowering_observations_out is not None:
                lowering_observations_out.append(
                    {
                        "rule_id": "uk_effect_text_patch_preimage_chain_ambiguous",
                        "family": "temporal_recovery",
                        "phase": "lowering",
                        "target": target,
                        "effective_date": effective_date,
                        "op_ids": tuple(op.op_id for op in group_ops),
                        "reason_code": "same_target_text_patch_preimage_chain_not_unique",
                        "reason": (
                            "UK same-target text patches had exact preimage-chain "
                            "links, but the chain was not unique; lowering left the "
                            "original order intact rather than guessing precedence."
                        ),
                        "blocking": True,
                        "strict_disposition": "block",
                        "quirks_disposition": "record",
                    }
                )
            continue
        ready = [idx for idx in range(len(group_ops)) if not predecessors[idx]]
        topo: list[int] = []
        remaining_successors = {idx: set(items) for idx, items in successors.items()}
        remaining_predecessors = {idx: set(items) for idx, items in predecessors.items()}
        while ready:
            node_idx = ready.pop(0)
            topo.append(node_idx)
            for succ_idx in sorted(remaining_successors[node_idx]):
                remaining_predecessors[succ_idx].discard(node_idx)
                if not remaining_predecessors[succ_idx]:
                    ready.append(succ_idx)
            ready.sort(key=lambda i: indices[i])
        if len(topo) != len(group_ops):
            if lowering_observations_out is not None:
                lowering_observations_out.append(
                    {
                        "rule_id": "uk_effect_text_patch_preimage_chain_ambiguous",
                        "family": "temporal_recovery",
                        "phase": "lowering",
                        "target": target,
                        "effective_date": effective_date,
                        "op_ids": tuple(op.op_id for op in group_ops),
                        "reason_code": "same_target_text_patch_preimage_chain_cycle",
                        "reason": (
                            "UK same-target text patches had cyclic exact preimage-chain "
                            "links; lowering left the original order intact."
                        ),
                        "blocking": True,
                        "strict_disposition": "block",
                        "quirks_disposition": "record",
                    }
                )
            continue
        reordered_group = [group_ops[idx] for idx in topo]
        if [op.op_id for op in reordered_group] == [op.op_id for op in group_ops]:
            continue
        for target_slot, op in zip(indices, reordered_group):
            ordered[target_slot] = op
        if lowering_observations_out is not None:
            lowering_observations_out.append(
                {
                    "rule_id": "uk_effect_text_patch_preimage_chain_ordered",
                    "family": "temporal_recovery",
                    "phase": "lowering",
                    "target": target,
                    "effective_date": effective_date,
                    "original_op_ids": tuple(op.op_id for op in group_ops),
                    "ordered_op_ids": tuple(op.op_id for op in reordered_group),
                    "reason_code": "exact_same_target_text_patch_preimage_chain",
                    "reason": (
                        "UK same-target text patches were ordered by exact quoted "
                        "preimage chain: one replacement text is the next patch's "
                        "source preimage."
                    ),
                    "blocking": False,
                    "strict_disposition": "record",
                    "quirks_disposition": "record",
                }
            )
    return ordered
