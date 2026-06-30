"""Ordering helpers for UK effect replay and text-patch lowering."""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, NamedTuple, Optional, Sequence

from lawvm.core.cross_act_same_moment import (
    _SameMomentTargetKey as _CoreSameMomentTargetKey,
    detect_same_moment_conflict_groups_generic,
)
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


# Wave 0b: the same-moment conflict key is the SHARED kernel key
# (``core.cross_act_same_moment._SameMomentTargetKey``), reused here so UK's
# detection delegates to ``detect_same_moment_conflict_groups_generic`` and the
# precedence-winner index keys match the kernel's conflict-group keys exactly
# (one definition, no fork). Aliased to the historic UK name for the call sites
# that build a lookup key (``_precedence_rank`` / the winners index).
_SameMomentTargetKey = _CoreSameMomentTargetKey


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
    same_moment_precedence_claims: Optional[Sequence[Any]] = None,
) -> list[UKEffectRecord]:
    """Order UK effects by legal time and affecting-source citation order.

    ``same_moment_precedence_claims`` is opt-in (§1.7): a sequence of
    ``SameMomentPrecedenceClaim`` carriers. Only VALIDATED claims — bound to a
    real detected same-moment cross-act incompatible-payload conflict at their
    ``(effective_date, target)`` with exactly those conflicting acts — change
    ordering: for a resolved conflict the claimed winner's act is ordered ahead
    of ``affecting_act_id`` lexical order, and the ambiguity finding records
    ``resolved_by_claim`` instead of ``affecting_act_id_lexical_order_unproven``.
    With no claim authored, ordering and the finding are byte-unchanged.
    """

    original = list(effects)
    date_overrides = effective_date_overrides or {}

    def _effective_date(effect: UKEffectRecord) -> str:
        return date_overrides.get(effect.effect_id) or effect.effective_date

    # §1.7 precedence resolution: index VALIDATED precedence claims by the
    # (effective_date, affected_target) of the conflict they resolve, mapping to
    # the winning affecting act. With no claims authored the index is empty and
    # the precedence rank below is constant, so ordering is byte-unchanged.
    precedence_winner_by_conflict = _validated_same_moment_precedence_winners(
        original,
        effective_date_of=_effective_date,
        precedence_claims=same_moment_precedence_claims,
    )

    def _precedence_rank(e: UKEffectRecord) -> int:
        if not precedence_winner_by_conflict:
            return 0
        conflict_key = _SameMomentTargetKey(
            effective_date=_effective_date(e) or "",
            affected_target=str(e.affected_provisions or "").strip(),
        )
        winner_act = precedence_winner_by_conflict.get(conflict_key)
        if winner_act is None:
            return 0
        # Winner's act sorts ahead of every other conflicting act; ties among the
        # winner's own effects, and among the losers, fall through to the existing
        # source-provision/effect-id key, so the change is minimal and stable.
        return 0 if e.affecting_act_id == winner_act else 1

    def _sort_key(e: UKEffectRecord) -> tuple[Any, ...]:
        return (
            _effective_date(e) or "9999-99-99",
            str(e.modified or ""),
            _precedence_rank(e),
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
        precedence_winner_by_conflict=precedence_winner_by_conflict,
    )
    return ordered


def _uk_effect_effective_date(effect: UKEffectRecord, effective_date_of: Any) -> str:
    return effective_date_of(effect) or ""


def _uk_effect_affected_target(effect: UKEffectRecord) -> str:
    return str(effect.affected_provisions or "").strip()


def _uk_effect_affecting_act_id(effect: UKEffectRecord) -> str:
    return effect.affecting_act_id


def _detect_same_moment_conflict_groups(
    original: Sequence[UKEffectRecord],
    *,
    effective_date_of: Any,
) -> dict[_SameMomentTargetKey, list[tuple[UKEffectRecord, UKEffectRecord]]]:
    """Return the same-moment cross-act incompatible-payload conflicts.

    Wave 0b (``notes/CORE_PIPELINE_UNIFICATION_DESIGN.md`` §2.1.1, divergence
    #1): the detection ALGORITHM — group by ``(effective_date, affected_target)``,
    require ≥2 distinct affecting acts, pair distinct-act effects whose payloads
    collide — is the SHARED kernel
    :func:`lawvm.core.cross_act_same_moment.detect_same_moment_conflict_groups_generic`,
    NOT a UK fork. UK supplies only the jurisdiction-specific inputs (like EE's
    predicate): the effect-level accessors (``affected_provisions`` string,
    ``affecting_act_id``, override-aware effective date) and the effect-type
    incompatibility predicate ``_uk_same_moment_payloads_incompatible``.

    UK runs same-moment detection at the EFFECT level (before lowering — ops
    are not yet built at ``_order_uk_effects_for_replay`` time; see the §2.1
    impedance note), so it binds ``R = UKEffectRecord`` rather than the op
    accessors the SE/EE/NO frontends bind. The returned conflict groups feed
    both the ambiguity finding and the precedence-claim binding, unchanged.

    The shared algorithm buckets by affecting act before pairing; UK's old loop
    paired by raw index. The finding is built from the de-duplicated, sorted
    participant SET, which is identical between the two — proven byte-for-byte
    by ``tests/test_uk_order_ops_parallel_run.py``.
    """
    return detect_same_moment_conflict_groups_generic(
        list(original),
        effective_date_of=lambda effect: _uk_effect_effective_date(
            effect, effective_date_of
        ),
        affecting_act_id_of=_uk_effect_affecting_act_id,
        affected_target_of=_uk_effect_affected_target,
        incompatible_payload_predicate=_uk_same_moment_payloads_incompatible,
    )


def conflicts_from_effects(
    effects: Sequence[UKEffectRecord],
    *,
    effective_date_overrides: Optional[Mapping[str, str]] = None,
) -> list[Any]:
    """Return ``DetectedSameMomentConflict`` carriers for a set of UK effects.

    This is the binding surface a ``SameMomentPrecedenceClaim`` validates
    against: the same detection the ambiguity finding uses, exposed as typed
    carriers (``effective_date``, ``affected_target``, the conflicting affecting
    acts, and the conflicting effect ids). It never authors a winner.
    """
    # Imported lazily to avoid a module import cycle: the claim module imports
    # only this function's output type, and ordering is imported by replay.
    from lawvm.uk_legislation.same_moment_precedence_claim import (
        DetectedSameMomentConflict,
    )

    date_overrides = effective_date_overrides or {}

    def _effective_date(effect: UKEffectRecord) -> str:
        return date_overrides.get(effect.effect_id) or effect.effective_date

    conflict_groups = _detect_same_moment_conflict_groups(
        list(effects), effective_date_of=_effective_date
    )
    detected: list[DetectedSameMomentConflict] = []
    for key, conflicting_pairs in conflict_groups.items():
        conflicting_effects = {
            id(e): e for pair in conflicting_pairs for e in pair
        }.values()
        effect_ids_by_act: dict[str, list[str]] = {}
        for effect in conflicting_effects:
            effect_ids_by_act.setdefault(effect.affecting_act_id, []).append(
                effect.effect_id
            )
        detected.append(
            DetectedSameMomentConflict(
                effective_date=key.effective_date,
                affected_target=key.affected_target,
                conflicting_affecting_acts=tuple(sorted(effect_ids_by_act)),
                conflicting_effect_ids=tuple(
                    sorted(e.effect_id for e in conflicting_effects)
                ),
                effect_ids_by_act={
                    act: tuple(ids) for act, ids in effect_ids_by_act.items()
                },
            )
        )
    return detected


def _validated_same_moment_precedence_winners(
    original: Sequence[UKEffectRecord],
    *,
    effective_date_of: Any,
    precedence_claims: Optional[Sequence[Any]],
) -> dict[_SameMomentTargetKey, str]:
    """Index validated precedence claims by conflict key → winning affecting act.

    Each claim is validated against the REAL detected conflicts (reusing the
    shared detection); only a claim that binds to an actual conflict with exactly
    those acts and a recognized winner/basis contributes a winner. With no claims
    the result is empty and ordering/findings are byte-unchanged.
    """
    if not precedence_claims:
        return {}
    # Lazy import to avoid an import cycle at module load.
    from lawvm.uk_legislation.same_moment_precedence_claim import (
        validate_same_moment_precedence_claim,
    )

    detected = conflicts_from_effects(
        original,
        effective_date_overrides={
            e.effect_id: effective_date_of(e)
            for e in original
            if effective_date_of(e)
        },
    )
    winners: dict[_SameMomentTargetKey, str] = {}
    for claim in precedence_claims:
        validation = validate_same_moment_precedence_claim(
            claim, detected_conflicts=detected
        )
        if not validation.validated:
            continue
        key = _SameMomentTargetKey(
            effective_date=claim.effective_date,
            affected_target=str(claim.affected_target or "").strip(),
        )
        winners[key] = claim.winner_affecting_act_id
    return winners


def _emit_uk_same_moment_cross_act_conflict_findings(
    original: Sequence[UKEffectRecord],
    ordered: Sequence[UKEffectRecord],
    *,
    effective_date_of: Any,
    diagnostics_out: Optional[list[dict[str, Any]]],
    lowering_observations_out: Optional[list[dict[str, Any]]],
    precedence_winner_by_conflict: Optional[dict[_SameMomentTargetKey, str]] = None,
) -> None:
    """Emit a §1.7 ambiguity finding for same-moment cross-act incompatible payloads.

    When two effects share the same effective date and affected target but come
    from DIFFERENT affecting acts and carry incompatible whole-target payloads
    (see ``_uk_same_moment_payloads_incompatible``), the materialized winner is
    today decided only by ``affecting_act_id`` lexical order in ``_sort_key``.
    That is "legal conflict resolved by Python accident" (§1.7): an ambiguity
    until a precedence rule proves otherwise.

    The finding is ADDITIVE in the no-claim case: it records that the pick is
    order-based and unproven so the conflict is visible (§1.8/§8) and rejectable
    under strict mode (§1.7/§14). When a VALIDATED ``SameMomentPrecedenceClaim``
    resolves the conflict (``precedence_winner_by_conflict``), the finding instead
    records ``resolved_by_claim`` with the claimed winning act.
    """
    if diagnostics_out is None and lowering_observations_out is None:
        return

    winner_by_conflict = precedence_winner_by_conflict or {}
    conflict_groups = _detect_same_moment_conflict_groups(
        original, effective_date_of=effective_date_of
    )
    for key, conflicting_pairs in conflict_groups.items():
        conflicting_acts = sorted(
            {effect.affecting_act_id for pair in conflicting_pairs for effect in pair}
        )
        # The op that wins under the current order: the conflicting effect that
        # sorts first in the already-computed replay order. When a validated
        # precedence claim resolved this conflict, ``ordered`` already places the
        # claimed winner's act first, so this is the claimed winner.
        ordered_conflicting = [
            effect
            for effect in ordered
            if any(effect is pair_effect for pair in conflicting_pairs for pair_effect in pair)
        ]
        order_based_winner = ordered_conflicting[0] if ordered_conflicting else None
        claimed_winner_act = winner_by_conflict.get(key)
        if claimed_winner_act is not None:
            resolution = "resolved_by_claim"
        else:
            resolution = "affecting_act_id_lexical_order_unproven"
        record = _uk_ordering_diagnostic(
            rule_id="uk_same_moment_cross_act_incompatible_payload_ambiguous",
            reason=(
                "Two or more affecting acts change the same target at the same "
                "effective date with incompatible whole-target payloads. "
                + (
                    "A validated same-moment precedence claim proves which act "
                    "prevails; the materialized winner follows the claim."
                    if claimed_winner_act is not None
                    else (
                        "The materialized winner is currently chosen by "
                        "affecting_act_id lexical order with no precedence rule; "
                        "this is a §1.7 ambiguity until a precedence claim proves "
                        "which act prevails."
                    )
                )
            ),
            blocking=claimed_winner_act is None,
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
            resolution=resolution,
            **(
                {"resolved_by_claim_winner_affecting_act_id": claimed_winner_act}
                if claimed_winner_act is not None
                else {}
            ),
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
