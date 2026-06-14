"""Ordering helpers for UK effect replay and text-patch lowering."""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, NamedTuple, Optional, Sequence

from lawvm.core.diagnostic_records import diagnostic_detail
from lawvm.core.ir import LegalOperation
from lawvm.core.semantic_types import TextPatchKindEnum
from lawvm.roman import roman_to_arabic as _shared_roman_to_arabic
from lawvm.uk_legislation.addressing import _action_name
from lawvm.uk_legislation.effects import STRUCTURAL_EFFECT_TYPES, UKEffectRecord
from lawvm.uk_legislation.uk_grafter import _clean_num

# §1.7 same-moment cross-act conflict classification.
#
# At ordering time only the effect-feed record is available (not the lowered
# payload), so "incompatible payload" is decided from the effect TYPE family,
# conservatively. Two effects from DISTINCT affecting acts on the SAME
# (effective_date, affected target) are incompatible when their whole-target
# outcomes cannot both stand at the same instant:
#
#   * a whole-target DESTRUCTIVE effect (repeal/omit of the whole provision)
#     against ANY other structural change to that provision — you cannot both
#     delete the provision and amend it at the same moment, and the materialized
#     result depends purely on which act the ordering happens to run last;
#   * two whole-target REPLACEMENT effects (substitution of the whole provision)
#     — each replaces the entire provision with different text, so only one can
#     win and the winner is order-determined.
#
# Word/fragment-level changes (``word(s) inserted/substituted/omitted`` etc.) are
# scoped to a fragment, not the whole provision, so two such effects from
# different acts can legitimately coexist; they are intentionally NOT treated as
# incompatible here to avoid false ambiguity findings.
_UK_WHOLE_TARGET_DESTRUCTIVE_EFFECT_TYPES = frozenset(
    {
        "repealed",
        "entry repealed",
        "repealed in part",
        "omitted",
        "entry omitted",
    }
)
_UK_WHOLE_TARGET_REPLACEMENT_EFFECT_TYPES = frozenset(
    {
        "substituted",
        "entry substituted",
    }
)
_UK_WHOLE_TARGET_STRUCTURAL_EFFECT_TYPES = (
    _UK_WHOLE_TARGET_DESTRUCTIVE_EFFECT_TYPES | _UK_WHOLE_TARGET_REPLACEMENT_EFFECT_TYPES
)


def _uk_normalized_effect_type(effect: UKEffectRecord) -> str:
    return " ".join(str(effect.effect_type or "").strip().lower().split())


def _uk_same_moment_payloads_incompatible(
    left: UKEffectRecord, right: UKEffectRecord
) -> bool:
    """Return True when two same-(date, target) cross-act effects cannot coexist.

    Sound and conservative: only whole-target destructive/replacement families
    are treated as incompatible (see the family comment above). Word/fragment
    effects, and any non-structural effect (e.g. a ``coming into force``
    commencement entry that changes no text), return False.
    """
    left_type = _uk_normalized_effect_type(left)
    right_type = _uk_normalized_effect_type(right)
    # Both effects must actually change the target's text for them to compete.
    # A commencement/non-structural entry on the same target is not a payload.
    if left_type not in STRUCTURAL_EFFECT_TYPES or right_type not in STRUCTURAL_EFFECT_TYPES:
        return False
    left_whole = left_type in _UK_WHOLE_TARGET_STRUCTURAL_EFFECT_TYPES
    right_whole = right_type in _UK_WHOLE_TARGET_STRUCTURAL_EFFECT_TYPES
    if not left_whole and not right_whole:
        return False
    left_destructive = left_type in _UK_WHOLE_TARGET_DESTRUCTIVE_EFFECT_TYPES
    right_destructive = right_type in _UK_WHOLE_TARGET_DESTRUCTIVE_EFFECT_TYPES
    # A whole-target repeal/omission against any other structural change to the
    # same provision is incompatible: you cannot both delete the provision and
    # amend it at the same instant.
    if left_destructive or right_destructive:
        return True
    # Otherwise both are whole-target replacements: two distinct substitutions of
    # the same provision each overwrite it, so only one can win.
    return True


_UK_SOURCE_PROVISION_ORDER_TOKEN_RE = re.compile(
    r"\b(?:regs?|regulations?|rules?|articles?|arts?|sections?|ss?|s|"
    r"schedules?|schs?|sch|paragraphs?|paras?|para)\.?\s*(?P<label>[0-9]+[A-Za-z]*)"
    r"|\((?P<paren>[0-9A-Za-z]+)\)"
)
_UK_SOURCE_PROVISION_LABEL_ALNUM_RE = re.compile(r"[^0-9A-Za-z]+")
_UK_SOURCE_PROVISION_NUMERIC_SUFFIX_RE = re.compile(r"(\d+)([a-z]*)")


def _uk_ordering_diagnostic(
    *,
    rule_id: str,
    reason: str,
    blocking: bool,
    **detail: Any,
) -> dict[str, Any]:
    return diagnostic_detail(
        rule_id=rule_id,
        family="temporal_recovery",
        phase="lowering",
        reason=reason,
        blocking=blocking,
        detail=detail,
    )


class _EffectOrderingGroupKey(NamedTuple):
    effective_date: str
    affected_target: str
    affecting_act_id: str


class _SameMomentTargetKey(NamedTuple):
    """Group key for cross-act same-moment conflict detection (no act in key)."""

    effective_date: str
    affected_target: str


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
    token = _UK_SOURCE_PROVISION_LABEL_ALNUM_RE.sub("", str(label or "")).lower()
    if not token:
        return (9, "")
    match = _UK_SOURCE_PROVISION_NUMERIC_SUFFIX_RE.fullmatch(token)
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
    tokens: list[tuple[Any, ...]] = []
    previous_alpha = False
    for match in _UK_SOURCE_PROVISION_ORDER_TOKEN_RE.finditer(text):
        raw_label = match.group("label") or match.group("paren") or ""
        key = _uk_source_provision_label_sort_key(raw_label, previous_alpha=previous_alpha)
        tokens.append(key)
        previous_alpha = bool(key and key[0] == 1)
    return (tuple(tokens), text)


def _order_uk_effects_for_replay(
    effects: Sequence[UKEffectRecord],
    *,
    effective_date_overrides: Optional[Mapping[str, str]] = None,
    diagnostics_out: Optional[list[dict[str, Any]]] = None,
    lowering_observations_out: Optional[list[dict[str, Any]]] = None,
) -> list[UKEffectRecord]:
    """Order UK effects by legal time and affecting-source citation order."""

    original = list(effects)
    date_overrides = effective_date_overrides or {}

    def _effective_date(effect: UKEffectRecord) -> str:
        return date_overrides.get(effect.effect_id) or effect.effective_date

    def _sort_key(e: UKEffectRecord) -> tuple[Any, ...]:
        return (
            _effective_date(e) or "9999-99-99",
            str(e.modified or ""),
            e.affecting_act_id,
            _uk_source_provision_order_key(e.affecting_provisions),
            e.effect_id,
        )

    ordered = sorted(original, key=_sort_key)
    if diagnostics_out is None and lowering_observations_out is None:
        return ordered

    groups: dict[_EffectOrderingGroupKey, list[UKEffectRecord]] = {}
    for effect in original:
        group_key = _EffectOrderingGroupKey(
            effective_date=_effective_date(effect) or "9999-99-99",
            affected_target=str(effect.affected_provisions or ""),
            affecting_act_id=effect.affecting_act_id,
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
        record = _uk_ordering_diagnostic(
            rule_id="uk_effect_source_provision_order_normalized",
            reason=(
                "UK effects with the same effective date, affected target, and "
                "affecting act were ordered by source provision citation rather "
                "than opaque effect id"
            ),
            blocking=False,
            effective_date=group_key[0],
            affected_target=group_key[1],
            affecting_act_id=group_key[2],
            reason_code="same_date_same_affecting_act_source_citation_order",
            original_effect_ids=tuple(old_ids),
            ordered_effect_ids=tuple(new_ids),
            original_affecting_provisions=tuple(effect.affecting_provisions for effect in group_effects),
            ordered_affecting_provisions=tuple(effect.affecting_provisions for effect in new_group),
        )
        if diagnostics_out is not None:
            diagnostics_out.append(record)
        if lowering_observations_out is not None:
            lowering_observations_out.append(dict(record))

    _emit_uk_same_moment_cross_act_conflict_findings(
        original,
        ordered,
        effective_date_of=_effective_date,
        diagnostics_out=diagnostics_out,
        lowering_observations_out=lowering_observations_out,
    )
    return ordered


def _emit_uk_same_moment_cross_act_conflict_findings(
    original: Sequence[UKEffectRecord],
    ordered: Sequence[UKEffectRecord],
    *,
    effective_date_of: Any,
    diagnostics_out: Optional[list[dict[str, Any]]],
    lowering_observations_out: Optional[list[dict[str, Any]]],
) -> None:
    """Emit a §1.7 ambiguity finding for same-moment cross-act incompatible payloads.

    When two effects share the same effective date and affected target but come
    from DIFFERENT affecting acts and carry incompatible whole-target payloads
    (see ``_uk_same_moment_payloads_incompatible``), the materialized winner is
    today decided only by ``affecting_act_id`` lexical order in ``_sort_key``.
    That is "legal conflict resolved by Python accident" (§1.7): an ambiguity
    until a precedence rule proves otherwise.

    This finding is ADDITIVE: it does not reorder effects or change which op
    wins. It records that the pick is order-based and unproven so the conflict is
    visible (§1.8/§8) and rejectable under strict mode (§1.7/§14). A future
    precedence claim resolves it.
    """
    if diagnostics_out is None and lowering_observations_out is None:
        return

    target_groups: dict[_SameMomentTargetKey, list[UKEffectRecord]] = {}
    for effect in original:
        target = str(effect.affected_provisions or "").strip()
        if not target:
            # No affected target to scope the conflict to; nothing to compare.
            continue
        effective_date = effective_date_of(effect) or ""
        if not effective_date:
            # Undated effects are not a same-EFFECTIVE-DATE conflict: bucketing
            # them together would manufacture a false ambiguity from the absence
            # of a date rather than a genuine same-moment collision. Their
            # ordering is handled (and date-recovery flagged) by other lanes.
            continue
        key = _SameMomentTargetKey(
            effective_date=effective_date,
            affected_target=target,
        )
        target_groups.setdefault(key, []).append(effect)

    for key, group_effects in target_groups.items():
        distinct_acts = {effect.affecting_act_id for effect in group_effects}
        if len(distinct_acts) < 2:
            continue
        conflicting_pairs: list[tuple[UKEffectRecord, UKEffectRecord]] = []
        for left_idx in range(len(group_effects)):
            for right_idx in range(left_idx + 1, len(group_effects)):
                left = group_effects[left_idx]
                right = group_effects[right_idx]
                if left.affecting_act_id == right.affecting_act_id:
                    continue
                if _uk_same_moment_payloads_incompatible(left, right):
                    conflicting_pairs.append((left, right))
        if not conflicting_pairs:
            continue
        conflicting_acts = sorted(
            {effect.affecting_act_id for pair in conflicting_pairs for effect in pair}
        )
        # The op that wins under the current order-based pick: the conflicting
        # effect that sorts first in the already-computed replay order.
        ordered_conflicting = [
            effect
            for effect in ordered
            if any(effect is pair_effect for pair in conflicting_pairs for pair_effect in pair)
        ]
        order_based_winner = ordered_conflicting[0] if ordered_conflicting else None
        record = _uk_ordering_diagnostic(
            rule_id="uk_same_moment_cross_act_incompatible_payload_ambiguous",
            reason=(
                "Two or more affecting acts change the same target at the same "
                "effective date with incompatible whole-target payloads. The "
                "materialized winner is currently chosen by affecting_act_id "
                "lexical order with no precedence rule; this is a §1.7 ambiguity "
                "until a precedence claim proves which act prevails."
            ),
            blocking=True,
            effective_date=key.effective_date,
            affected_target=key.affected_target,
            reason_code="same_moment_cross_act_incompatible_payload",
            conflicting_affecting_acts=tuple(conflicting_acts),
            conflicting_effects=tuple(
                {
                    "effect_id": effect.effect_id,
                    "affecting_act_id": effect.affecting_act_id,
                    "effect_type": _uk_normalized_effect_type(effect),
                    "affecting_provisions": effect.affecting_provisions,
                }
                for effect in sorted(
                    {id(e): e for pair in conflicting_pairs for e in pair}.values(),
                    key=lambda e: (e.affecting_act_id, e.effect_id),
                )
            ),
            order_based_winner_effect_id=(
                order_based_winner.effect_id if order_based_winner is not None else ""
            ),
            order_based_winner_affecting_act_id=(
                order_based_winner.affecting_act_id if order_based_winner is not None else ""
            ),
            resolution="affecting_act_id_lexical_order_unproven",
        )
        if diagnostics_out is not None:
            diagnostics_out.append(record)
        if lowering_observations_out is not None:
            lowering_observations_out.append(dict(record))


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
                    _uk_ordering_diagnostic(
                        rule_id="uk_effect_text_patch_preimage_chain_ambiguous",
                        reason=(
                            "UK same-target text patches had exact preimage-chain "
                            "links, but the chain was not unique; lowering left the "
                            "original order intact rather than guessing precedence."
                        ),
                        blocking=True,
                        target=target,
                        effective_date=effective_date,
                        op_ids=tuple(op.op_id for op in group_ops),
                        reason_code="same_target_text_patch_preimage_chain_not_unique",
                    )
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
                    _uk_ordering_diagnostic(
                        rule_id="uk_effect_text_patch_preimage_chain_ambiguous",
                        reason=(
                            "UK same-target text patches had cyclic exact preimage-chain "
                            "links; lowering left the original order intact."
                        ),
                        blocking=True,
                        target=target,
                        effective_date=effective_date,
                        op_ids=tuple(op.op_id for op in group_ops),
                        reason_code="same_target_text_patch_preimage_chain_cycle",
                    )
                )
            continue
        reordered_group = [group_ops[idx] for idx in topo]
        if [op.op_id for op in reordered_group] == [op.op_id for op in group_ops]:
            continue
        for target_slot, op in zip(indices, reordered_group, strict=True):
            ordered[target_slot] = op
        if lowering_observations_out is not None:
            lowering_observations_out.append(
                _uk_ordering_diagnostic(
                    rule_id="uk_effect_text_patch_preimage_chain_ordered",
                    reason=(
                        "UK same-target text patches were ordered by exact quoted "
                        "preimage chain: one replacement text is the next patch's "
                        "source preimage."
                    ),
                    blocking=False,
                    target=target,
                    effective_date=effective_date,
                    original_op_ids=tuple(op.op_id for op in group_ops),
                    ordered_op_ids=tuple(op.op_id for op in reordered_group),
                    reason_code="exact_same_target_text_patch_preimage_chain",
                )
            )
    return ordered
