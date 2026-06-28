"""§1.7 same-moment cross-act conflict detection for the Estonia (EE) frontend.

The classic ``apply_ee_ops`` applies ops in ``op.sequence`` order (with a
text-replace run-sort and persistent-postpass runoff). When two amendment
acts effect the SAME target on the SAME effective date with incompatible
whole-target payloads, the materialized winner is decided silently by
sequence order — a §1.7 "legal conflict resolved by Python accident."

This module mirrors the UK precedent
(``uk_same_moment_cross_act_incompatible_payload_ambiguous``) as an additive
pre-pass BEFORE the apply fold: detect incompatible-payload collisions and
emit a BLOCKING finding so the conflict is visible (§1.8) and rejectable
under strict mode (§1.7). Apply order itself is UNCHANGED — the existing
sequence-based ordering stays, so non-ambiguous cases are byte-identical to
the pre-detection path. For ambiguous cases the last-sequenced-wins pick
stays too, but the blocking finding makes the silent pick visible rather
than guessed away.

Incompatible payload is decided conservatively (mirroring the UK detector):

  * A whole-target DESTRUCTIVE action (``REPEAL`` of the whole provision)
    against ANY other structural change to that provision — you cannot both
    delete the provision and amend it at the same moment, and the materialized
    result depends purely on which op the apply fold happens to run last.
  * Two whole-target REPLACEMENT actions (``REPLACE`` on the whole provision)
    — each replaces the entire provision with different text, so only one can
    win and the winner is order-determined.

Fragment-level changes (``TEXT_REPLACE``), ``RENUMBER`` moves (their target is
identity-distinct from their destination), ``HEADING``/``META`` ops, and
``INSERT``s at distinct positions are intentionally NOT treated as
incompatible here, to avoid manufacturing false ambiguity from coexistence.
Two ``REPEAL``s of the same target from different acts are also NOT treated as
incompatible — they are redundant destructive effects with the same outcome,
not order-determining.

EE has no validated precedence-rule registry yet; every detected conflict
emits ``resolution: "sequence_order_unproven"``. Per §0 (preserve uncertainty),
do NOT magically pick rules. When EE grows a validated same-moment
precedence-claim family, the ``resolution`` field will follow the UK's
``resolved_by_claim`` shape; today only ``sequence_order_unproven`` is emitted.
"""
from __future__ import annotations

from collections.abc import Sequence as AbcSequence
from typing import Any, NamedTuple, Optional

from lawvm.core.diagnostic_records import diagnostic_detail
from lawvm.core.ir import LegalOperation
from lawvm.replay_adjudication import CompileAdjudication

# Rule id for the §1.7 ambiguity finding this module emits.
EE_SAME_MOMENT_AMBIGUITY_RULE_ID = "ee_same_moment_cross_act_incompatible_payload_ambiguous"


class _EESameMomentTargetKey(NamedTuple):
    """Group key for cross-act same-moment conflict detection (no act in key).

    Mirrors the UK ``_SameMomentTargetKey``: the act identifier is intentionally
    NOT part of the key, because a same-moment conflict is a property of the
    (date, target) bucket that survives regardless of how many acts collide.
    """

    effective_date: str
    affected_target: str


# Whole-target action families classified conservatively for incompatibility.
# These mirror the UK ``_UK_WHOLE_TARGET_*_EFFECT_TYPES`` sets, translated from
# effect-feed type strings to canonical LegalOperation action strings.
_EE_WHOLE_TARGET_DESTRUCTIVE_ACTIONS = frozenset({"repeal"})
_EE_WHOLE_TARGET_REPLACEMENT_ACTIONS = frozenset({"replace"})
_EE_WHOLE_TARGET_STRUCTURAL_ACTIONS = (
    _EE_WHOLE_TARGET_DESTRUCTIVE_ACTIONS | _EE_WHOLE_TARGET_REPLACEMENT_ACTIONS
)


def _ee_action_value(op: LegalOperation) -> str:
    """Return the canonical string for an op's action, enum or string either way."""
    action = op.action
    if hasattr(action, "value"):
        return str(action.value or "")
    return str(action or "")


def _ee_op_effective_date(op: LegalOperation) -> str:
    """Return the source-side effective date provenance string ("" if absent).

    Per AGENTS.md §3 (Phase contract), effective-date authority is carried by
    ``OperationSource.effective`` in lowering — a provenance field, not the
    authoritative ``TemporalEvent``/``ProvisionVersion`` lane. For §1.7
    same-moment detection in apply, the source-side effective date is the right
    grouping key (it is exactly the field the existing sequence-based apply
    path would have used). Its absence means the op is undated at apply time —
    excluded from same-date bucketing to avoid manufacturing false ambiguity.
    """
    if op.source is None:
        return ""
    return str(getattr(op.source, "effective", "") or "")


def _ee_op_affecting_act_id(op: LegalOperation) -> str:
    """Return the affecting act id provenance string ("" if absent)."""
    if op.source is None:
        return ""
    return str(getattr(op.source, "statute_id", "") or "")


def _ee_op_affected_target(op: LegalOperation) -> str:
    """Return a stable string serialization of the op's structural target path.

    Empty path (statute-level/global) is treated as no structural target — it is
    fragment-level TEXT_REPLACE territory, not a §1.7 cross-act collision. The
    serialized string form is intentionally used (rather than the tuple itself)
    so two ops with the same target string but distinct tuple identities still
    bucket together — this matches the UK detector's ``affected_provisions``
    string-key shape. EE has no canonical ``affected_provisions`` string field
    on ops (the path tuple is the canonical target); the string rendering is a
    stable projection, never authoritative.
    """
    if op.target is None:
        return ""
    path = getattr(op.target, "path", ())
    if not path:
        return ""
    return str(path)


def _ee_same_moment_payloads_incompatible(
    left: LegalOperation, right: LegalOperation
) -> bool:
    """Return True when two same-(date, target) cross-act ops cannot coexist.

    Sound and conservative — mirrors the UK detector's
    ``_uk_same_moment_payloads_incompatible``. Only whole-target DESTRUCTIVE
    (REPEAL) and REPLACEMENT (REPLACE) actions are treated as incompatible.
    Fragment-level TEXT_REPLACE, RENUMBER moves, HEADING/META ops, and INSERTs
    at distinct positions can legitimately coexist at the same instant and are
    intentionally NOT flagged here, to avoid false ambiguity findings.

    Two REPEALs of the same target from different acts are also NOT
    incompatible — they are redundant destructive effects with the same
    outcome, not order-determining. (The UK detector's verification surface for
    ``repealed`` is a single shared group; flagging repeal+x_repeal would
    manufacture a finding that has no order-decided winner to dispute.)
    """
    left_action = _ee_action_value(left)
    right_action = _ee_action_value(right)
    left_whole = left_action in _EE_WHOLE_TARGET_STRUCTURAL_ACTIONS
    right_whole = right_action in _EE_WHOLE_TARGET_STRUCTURAL_ACTIONS
    if not left_whole and not right_whole:
        return False
    left_destructive = left_action in _EE_WHOLE_TARGET_DESTRUCTIVE_ACTIONS
    right_destructive = right_action in _EE_WHOLE_TARGET_DESTRUCTIVE_ACTIONS
    if left_destructive and right_destructive:
        # Two REPEALs are redundant destructive effects — same outcome, no
        # order-decided winner to dispute.
        return False
    # A whole-target REPEAL against any other structural change to the same
    # provision is incompatible: you cannot both delete it and amend it.
    if left_destructive or right_destructive:
        return True
    # Otherwise both are whole-target REPLACE: two distinct substitutions of
    # the same provision each overwrite it, so only one can win.
    return True


def detect_ee_same_moment_cross_act_conflicts(
    ops: AbcSequence[LegalOperation],
    *,
    adjudications_out: Optional[list[CompileAdjudication]] = None,
    lowering_observations_out: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """Emit blocking §1.7 ambiguity findings for same-moment cross-act EE conflicts.

    Pre-pass form (runs BEFORE the apply fold, per the UK precedent). Detection
    is ADDITIVE:

      * It does NOT change apply order — the existing sequence-based ordering
        stays, so non-ambiguous cases are byte-identical to the pre-detection
        path. For ambiguous cases the last-sequenced-wins pick stays; the
        finding makes the silent pick visible so strict mode can reject.
      * No op is rejected by the detector itself; both conflicting ops land in
        the apply fold as before. The finding is a cross-act evidence row, not
        a per-op skip — it carries an empty ``op_id`` so the conserved-wrapper
        partition (which keys per-op skips by ``op_id``) is unaffected.

    Returns the list of finding detail dicts. Also appends to
    ``adjudications_out`` (as a blocking ``CompileAdjudication``) and to
    ``lowering_observations_out`` (as a mirrored dict) — the same dual-surface
    emission shape the UK detector uses.

    Detects cross-act conflicts only: groups ops by
    ``(effective_date, affected_target)``, and within each multi-act group,
    finds pairs from distinct affecting acts whose whole-target payloads are
    incompatible per :func:`_ee_same_moment_payloads_incompatible`.
    """
    target_groups: dict[_EESameMomentTargetKey, list[LegalOperation]] = {}
    for op in ops:
        target = _ee_op_affected_target(op)
        if not target:
            # No structural target to scope the conflict to. Empty/globally
            # scoped ops cannot collide at a structural target level.
            continue
        effective_date = _ee_op_effective_date(op)
        if not effective_date:
            # Undated ops are not a same-EFFECTIVE-DATE conflict: bucketing
            # them together would manufacture a false ambiguity from the
            # absence of a date rather than a genuine same-moment collision.
            continue
        key = _EESameMomentTargetKey(
            effective_date=effective_date,
            affected_target=target,
        )
        target_groups.setdefault(key, []).append(op)

    findings: list[dict[str, Any]] = []
    for key, group_ops in target_groups.items():
        affects_to_ops: dict[str, list[LegalOperation]] = {}
        for op in group_ops:
            act_id = _ee_op_affecting_act_id(op)
            if not act_id:
                # Ops with no affecting-act provenance cannot participate in a
                # cross-act conflict (synthesized tests can end up here
                # accidentally). Excluded rather than grouped under "".
                continue
            affects_to_ops.setdefault(act_id, []).append(op)
        if len(affects_to_ops) < 2:
            continue

        conflicting_pairs: list[tuple[LegalOperation, LegalOperation]] = []
        acts_sorted = list(affects_to_ops.keys())
        for left_act_pos in range(len(acts_sorted)):
            for right_act_pos in range(left_act_pos + 1, len(acts_sorted)):
                for left in affects_to_ops[acts_sorted[left_act_pos]]:
                    for right in affects_to_ops[acts_sorted[right_act_pos]]:
                        if _ee_same_moment_payloads_incompatible(left, right):
                            conflicting_pairs.append((left, right))
        if not conflicting_pairs:
            continue

        # De-duplicate the conflict participation set by op_id. An op may pair
        # against multiple ops from the other act, but it must appear once.
        conflicting_ops_unique: list[LegalOperation] = []
        seen_op_ids: set[str] = set()
        for pair in conflicting_pairs:
            for op in pair:
                if op.op_id not in seen_op_ids:
                    seen_op_ids.add(op.op_id)
                    conflicting_ops_unique.append(op)

        conflicting_acts = sorted(
            {_ee_op_affecting_act_id(op) for op in conflicting_ops_unique}
        )
        record = diagnostic_detail(
            rule_id=EE_SAME_MOMENT_AMBIGUITY_RULE_ID,
            family="temporal_recovery",
            phase="apply",
            reason=(
                "Two or more affecting acts change the same target at the same "
                "effective date with incompatible whole-target payloads. The "
                "materialized winner is currently chosen by op.sequence with no "
                "precedence rule; this is a §1.7 ambiguity until a precedence "
                "claim proves which act prevails. Apply order is unchanged; the "
                "finding makes the silent pick visible and strict-rejectable."
            ),
            blocking=True,
            detail={
                "affected_target": key.affected_target,
                "effective_date": key.effective_date,
                "reason_code": "same_moment_cross_act_incompatible_payload",
                "resolution": "sequence_order_unproven",
                "conflicting_affecting_acts": tuple(conflicting_acts),
                "conflicting_ops": tuple(
                    {
                        "op_id": op.op_id,
                        "affecting_act_id": _ee_op_affecting_act_id(op),
                        "action": _ee_action_value(op),
                        "sequence": op.sequence,
                        "target": str(op.target),
                    }
                    for op in sorted(
                        conflicting_ops_unique,
                        key=lambda o: (o.sequence, o.op_id),
                    )
                ),
            },
        )
        findings.append(record)
        if adjudications_out is not None:
            adjudications_out.append(
                CompileAdjudication(
                    kind=EE_SAME_MOMENT_AMBIGUITY_RULE_ID,
                    message=str(record["reason"]),
                    source_statute="",
                    op_id="",
                    blocking=True,
                    phase=str(record.get("phase") or "apply"),
                    detail=record,
                )
            )
        if lowering_observations_out is not None:
            lowering_observations_out.append(dict(record))
    return findings
