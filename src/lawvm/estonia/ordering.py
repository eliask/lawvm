"""EE-specific surface for the §1.7 same-moment cross-act conflict detector.

Per AGENTS.md §2.5 (one-parser-per-family + named retirement plan), the
standalone EE detector that used to live here has been **retired** in favour of
the shared detector at :mod:`lawvm.core.cross_act_same_moment`. Production
calls in :func:`lawvm.estonia.grafter.apply_ee_ops` now route through
:func:`lawvm.core.cross_act_same_moment.detect_cross_act_same_moment_conflicts`
with ``finder_kind_prefix="ee"``.

What remains in this module is the EE-specific tail of the shared detector's
parameterization — the EE compatibility predicate that classifies whole-target
DESTRUCTIVE (``REPEAL``) and REPLACEMENT (``REPLACE``) actions as
order-determining. The shared module's *default* conservative predicate
(``_default_payloads_incompatible``) treats any operand in the fragment
allowlist as non-structural and short-circuits to ``False``; EE's predicate
instead excludes only the both-fragment case, so a REPEAL+TEXT_REPLACE pair is
incompatible per EE. The two predicates diverge on exactly that mixed
fragment-vs-structural case. EE supplies its own predicate explicitly when
calling the shared detector so the finding output is byte-identical to the
pre-migration standalone behaviour (verified by
``tests/test_ee_cross_act_same_moment_parity.py``).

Migration triage and the §2.5 trio (parity criteria / deletion schedule /
next-wave reconcile) live in ``notes/CROSS_ACT_SAME_MOMENT_MIGRATION_PLAN.md``.

Incompatible payload is decided conservatively (mirrors the UK detector):

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

from lawvm.core.ir import LegalOperation
from lawvm.core.op_ordering import OrderingProfile, default_temporal_key

# Rule id for the §1.7 ambiguity finding the shared module stamps with
# ``finder_kind_prefix="ee"``. Kept here as a stable import surface so existing
# test/observation imports (`from lawvm.estonia.ordering import
# EE_SAME_MOMENT_AMBIGUITY_RULE_ID`) survive the §2.5 retirement of the
# standalone EE detector. The string value mirror-equals the
# ``f"{finder_kind_prefix}_same_moment_cross_act_incompatible_payload_ambiguous"``
# the shared module emits for ``finder_kind_prefix="ee"``; the parity test in
# ``tests/test_ee_cross_act_same_moment_parity.py`` pins the equivalence.
EE_SAME_MOMENT_AMBIGUITY_RULE_ID = "ee_same_moment_cross_act_incompatible_payload_ambiguous"


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


def ee_same_moment_payloads_incompatible(
    left: LegalOperation, right: LegalOperation
) -> bool:
    """Return True when two same-(date, target) cross-act ops cannot coexist.

    EE's carrier predicate, supplied to the shared detector
    :func:`lawvm.core.cross_act_same_moment.detect_cross_act_same_moment_conflicts`
    via its ``incompatible_payload_predicate`` parameter. Sound and
    conservative — mirrors the UK detector's ``_uk_same_moment_payloads_incompatible``.
    Only whole-target DESTRUCTIVE (REPEAL) and REPLACEMENT (REPLACE) actions
    are treated as incompatible. Fragment-level TEXT_REPLACE, RENUMBER moves,
    HEADING/META ops, and INSERTs at distinct positions can legitimately
    coexist at the same instant and are intentionally NOT flagged here, to
    avoid false ambiguity findings.

    Two REPEALs of the same target from different acts are also NOT
    incompatible — they are redundant destructive effects with the same
    outcome, not order-determining. (The UK detector's verification surface for
    ``repealed`` is a single shared group; flagging repeal+x_repeal would
    manufacture a finding that has no order-decided winner to dispute.)

    Divergence note: this predicate differs from the shared module's default
    ``_default_payloads_incompatible`` on the mixed fragment-vs-structural case
    (e.g. REPEAL+TEXT_REPLACE). EE treats such a pair as incompatible (one is
    a whole-target destructive action against another structural change); the
    shared default treats it as not-incompatible (one operand is in the fragment
    allowlist). EE explicitly supplies this predicate to the shared detector so
    the finding output remains byte-identical to the pre-§2.5-retirement
    standalone behaviour — see
    ``notes/CROSS_ACT_SAME_MOMENT_MIGRATION_PLAN.md`` for the reconcile schedule.
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


def ee_ordering_profile() -> OrderingProfile:
    """The EE jurisdiction ordering profile fed to the unified kernel.

    Wave 0 (``notes/CORE_PIPELINE_UNIFICATION_DESIGN.md`` §3.2 / §4): EE's
    same-moment cross-act detection is the kernel-subsumed step, and this profile
    encodes EXACTLY the prior direct-detector contract so
    ``order_ops(ops, ee_ordering_profile())`` reproduces the old
    ``detect_cross_act_same_moment_conflicts`` call byte-for-byte:

    - ``finder_kind_prefix="ee"`` — the prefix the direct call used (so the
      finding ``kind`` ``ee_same_moment_cross_act_incompatible_payload_ambiguous``
      and the claim validation/rejection ``rule_id``s stay EE-distinct).
    - ``incompatible_payload_predicate=ee_same_moment_payloads_incompatible`` —
      EE's carrier predicate, which diverges from the shared default on the
      mixed fragment-vs-structural case (e.g. REPEAL+TEXT_REPLACE → incompatible
      per EE). Supplying it explicitly keeps the finding output identical to the
      pre-§2.5-retirement standalone behaviour.
    - ``temporal_key=default_temporal_key`` (sequence-identity) — EE has no
      commencement/effective dating lane in its APPLY-time ordering contract
      (same-moment bucketing reads ``OperationSource.effective`` inside the
      detector, not the kernel's temporal sort). The kernel's stable sort by
      ``(sequence, sequence)`` is therefore input order for EE's production ops,
      which are stamped with a monotonically ascending global sequence in
      chronological order upstream (``estonia/replay.py``). So the detector sees
      the same op order the old direct call did.
    - ``lex_posterior=False`` (implicit) — EE's same-moment unproven tiebreak is
      ``op.sequence`` (resolution label ``sequence_order_unproven``), NOT
      affecting-act lexical order (that is UK's). So no lex tiebreak.
    - no ``precedence_claims`` — EE has no validated precedence-rule registry yet
      (every detected conflict emits ``resolution: "sequence_order_unproven"``).
    - ``prospective_gate`` / ``renumber_vacate`` unset — later-wave hooks.

    NOTE — EE's longest-old-text-first text_replace run sort
    (``grafter._ee_text_replace_run_sort_key``) is a genuine EE drafting rule for
    SAME-SOURCE ``TEXT_REPLACE`` runs and is NOT same-moment cross-act ordering.
    It stays an EE frontend step in ``apply_ee_ops`` and is intentionally NOT
    expressed by this profile (the kernel subsumes same-moment detection +
    temporal/sequence ordering only).
    """
    return OrderingProfile(
        finder_kind_prefix="ee",
        incompatible_payload_predicate=ee_same_moment_payloads_incompatible,
        temporal_key=default_temporal_key,
        lex_posterior=False,
    )
