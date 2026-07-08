"""§1.7 same-moment cross-act incompatible-payload conflict detector (shared).

The classic apply-fold in every frontend (``apply_ee_ops``, ``apply_uk_*``,
``apply_no_ops``, ...) orders amendments by ``op.sequence`` (UK: by
``affecting_act_id`` lexical order) and emits the materialized winner. When two
amendment acts effect the SAME target on the SAME effective date with
incompatible whole-target payloads, the materialized winner is decided silently
by sequence/lexical order — a §1.7 "legal conflict resolved by Python accident."

This module is the **shared extraction** of the cross-act same-moment detector
that mirrors the EE ``detect_ee_same_moment_cross_act_conflicts`` detector at
``estonia/ordering.py:168`` and the UK ``_emit_uk_same_moment_cross_act_conflict_findings``
detector at ``uk_legislation/ordering.py:450``. Extracted under §2.6 rule-of-three:
the UK and EE detectors are the first two materialized instances and NO/EU/SE/NZ/US
have ZERO same-moment coverage; a third instance has now fired. This module
crystallizes the shared shape so the upcoming wave can refactor EE/UK to consume
it and extend to the remaining frontends.

**Anti-pattern (AGENTS.md §1.7):** same effective date + same target + incompatible
payload is ambiguity until a precedence rule proves otherwise. The detector does
NOT pick a winner — it emits a BLOCKING finding (so the conflict is visible per
§1.8 and strict-rejectable per §1.7/§14) OR, when a validated
``SameMomentPrecedenceClaim`` binds the conflict, records the validated winner
without ``blocking`` and emits ``resolution: "resolved_by_claim"``.

**Detection algorithm (mirrors EE, parameterized):**

  1. Group ops by ``(effective_date, affected_target)``. The act identifier is
     intentionally NOT part of the key (mirrors the UK ``_SameMomentTargetKey``):
     a same-moment conflict is a property of the ``(date, target)`` bucket that
     survives regardless of how many acts collide.
  2. Within each multi-act group, find pairs from DISTINCT affecting acts whose
     whole-target payloads are incompatible per the (overridable) compatibility
     predicate.
  3. For each detected conflict, look up matching ``precedence_claims``; if a
     validated claim binds the conflict, the finding records
     ``resolution: "resolved_by_claim"`` and ``blocking=False``; otherwise
     ``resolution: <unproven label>`` (default ``"sequence_order_unproven"``)
     and ``blocking=True``.

**Default compatibility predicate (conservative):** only whole-target
DESTRUCTIVE (``REPEAL``) and REPLACEMENT (``REPLACE``) actions are treated as
incompatible.

  * A whole-target ``REPEAL`` against any other structural change to that
    provision — you cannot both delete the provision and amend it at the same
    instant.
  * Two whole-target ``REPLACE`` actions — each replaces the entire provision
    with different text, so only one can win and the winner is order-determined.

Fragment-level changes (``TEXT_REPLACE``), ``RENUMBER`` moves (their target is
identity-distinct from their destination), ``HEADING``/``META`` ops, and
``INSERT``s at distinct positions are intentionally NOT treated as
incompatible here, to avoid manufacturing false ambiguity from coexistence. Two
``REPEAL``s of the same target from different acts are also NOT treated as
incompatible — they are redundant destructive effects with the same outcome,
not order-determining.

**Parameterization model:**

  * ``finder_kind_prefix`` (required): each frontend stamps its own short prefix
    (``"ee"``, ``"no"``, ``"eu"``, ``"se"``, ``"nz"``, ``"us"``) so the finding
    ``kind``, the precedence-claim validation ``rule_id``s, and the rejection
    ``rule_id``s are frontend-distinct (cross-frontend harmonization without
    collapsing one frontend's audit trail into another's).
  * ``incompatible_payload_predicate`` (optional, default None): a frontend can
    supply its own ``(op, op) -> bool`` comparator if its action vocabulary
    needs a different shape (e.g. a frontend with a ``SUBSTITUTE`` action that
    should be treated as incompatible). When None, the default conservative
    predicate is used (mirrors EE's ``_ee_same_moment_payloads_incompatible``,
    which itself mirrors the UK ``_uk_same_moment_payloads_incompatible``).
  * ``fragment_action_allowlist`` (optional, default None): actions a frontend
    treats as fragment-level/non-structural for the default predicate's
    incompatibility check. Defaults to the canonical fragment set
    (``TEXT_REPLACE``, ``TEXT_REPEAL``, ``HEADING_REPLACE``, ``META``,
    ``INSERT``, ``RENUMBER``); supply a frozenset to narrow or widen it.
  * ``precedence_claims`` (optional, default empty): typed claims binding a real
    detected conflict to an owned winning act + a recognized precedence basis.
    Validated by ``validate_same_moment_precedence_claim`` (mirrors UK's
    binding surface). When no validated claim matches a conflict, the finding
    records ``resolution: "sequence_order_unproven"`` (EE/NO/EU/SE/NZ/US today);
    when one matches, the finding records ``resolution: "resolved_by_claim"``.
  * ``unproven_resolution_label`` (optional, default
    ``"sequence_order_unproven"``): frontend-specific label for the no-claim
    case. UK uses ``"affecting_act_id_lexical_order_unproven"`` because UK's
    ordering tiebreak is ``affecting_act_id`` lexical order; the rest use the
    default ``"sequence_order_unproven"`` because their ordering tiebreak is
    ``op.sequence``.

**Carrier reuse:** this module defines its own op-level
``SameMomentPrecedenceClaim`` frozen dataclass (and supporting carriers) — NOT
importing from ``uk_legislation.same_moment_precedence_claim`` because AGENTS.md
§2.3 forbids core depending on a frontend module. UK validates the same legal
claim shape at effect level, so it adapts through the identity-neutral validation
records below rather than pretending effect ids are op ids.

**Dual-surface emission (mirrors EE/UK):** the detector optionally appends:

  * a blocking ``CompileAdjudication`` to ``adjudications_out`` — the
    per-statute evidence/adjudication lane consumed by the apply path.
  * a mirrored dict to ``lowering_observations_out`` — the lowering-observation
    surface consumed by the replay packet.

Both surfaces receive the same payload (the finding detail dict). The finding
has ``op_id=""`` (per EE's Pattern A: ``ee_replay_payload_after_eid``-style
at-``op_id=""``) so the per-op conserved-wrapper partition (which keys per-op
skips by ``op_id``) is unaffected — the finding is a cross-act evidence row,
not a per-op skip.

**Detection is ADDITIVE (mirrors EE/UK):**

  * It does NOT change apply order — the existing sequence-based ordering
    stays, so non-ambiguous cases are byte-identical to the pre-detection path.
    For ambiguous cases the last-sequenced-wins pick stays; the finding makes
    the silent pick visible so strict mode can reject.
  * No op is rejected by the detector itself; both conflicting ops land in the
    apply fold as before.

When a frontend grows a validated precedence-rule registry, the carrier and
validator in this module are the binding surface (mirrors UK's
``same_moment_precedence_claim`` module).
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence as AbcSequence
from dataclasses import dataclass, field
from typing import Any, Callable, NamedTuple, Optional, Sequence, TypeVar

from lawvm.core.diagnostic_records import diagnostic_detail
from lawvm.core.ir import LegalOperation
from lawvm.core.regex_safety import compile_classifier_regex
from lawvm.core.semantic_types import StructuralAction, legacy_text_action_value
from lawvm.replay_adjudication import CompileAdjudication

# Record-shape generic for the shared same-moment grouping algorithm. The op
# path binds ``R = LegalOperation``; UK binds ``R = UKEffectRecord`` (effect
# level, pre-lowering). The grouping algorithm never inspects ``R`` directly —
# only through the caller-supplied accessors (Wave 0b, design §2.1.1).
R = TypeVar("R")

__all__ = [
    "SAME_MOMENT_PRECEDENCE_CLAIM_KIND",
    "BASIS_LATER_ENACTMENT",
    "BASIS_DEVOLUTION_TERRITORIAL_EXTENT_SPLIT",
    "BASIS_EXPRESS_SAVING",
    "BASIS_EXPLICIT_PRECEDENCE_PROVISION",
    "RESOLUTION_RESOLVED_BY_CLAIM",
    "DEFAULT_UNPROVEN_RESOLUTION_LABEL",
    "SAME_MOMENT_CONFLICT_REASON_CODE",
    "same_moment_conflict_finding_kind",
    "DEFAULT_FRAGMENT_ACTIONS",
    "SameMomentPrecedenceClaim",
    "SameMomentPrecedenceClaimValidation",
    "DetectedSameMomentConflict",
    "SameMomentPrecedenceClaimRecord",
    "DetectedSameMomentConflictRecord",
    "SameMomentPrecedenceClaimValidationRecord",
    "detect_same_moment_conflict_groups_generic",
    "detected_same_moment_conflicts_from_ops",
    "validate_same_moment_precedence_claim_record",
    "validate_same_moment_precedence_claim",
    "detect_cross_act_same_moment_conflicts",
]

#─ Constants───────────────────────────────────────────────────────────────

# Recognized claim kind. Frontends construct claims with this kind; the
# validator rejects any other value (fail-loud per AGENTS.md §1.10).
SAME_MOMENT_PRECEDENCE_CLAIM_KIND = "same_moment_precedence"

# Recognized precedence bases (mirrors UK ``same_moment_precedence_claim``).
# The validator only accepts these named kinds; it never infers the basis from
# the conflict shape.
BASIS_LATER_ENACTMENT = "later_enactment"
BASIS_DEVOLUTION_TERRITORIAL_EXTENT_SPLIT = "devolution_territorial_extent_split"
BASIS_EXPRESS_SAVING = "express_saving"
BASIS_EXPLICIT_PRECEDENCE_PROVISION = "explicit_precedence_provision"
_RECOGNIZED_BASES = frozenset(
    {
        BASIS_LATER_ENACTMENT,
        BASIS_DEVOLUTION_TERRITORIAL_EXTENT_SPLIT,
        BASIS_EXPRESS_SAVING,
        BASIS_EXPLICIT_PRECEDENCE_PROVISION,
    }
)

# Resolution labels — the ``resolution`` field of the emitted finding detail.
RESOLUTION_RESOLVED_BY_CLAIM = "resolved_by_claim"
DEFAULT_UNPROVEN_RESOLUTION_LABEL = "sequence_order_unproven"
SAME_MOMENT_CONFLICT_REASON_CODE = "same_moment_cross_act_incompatible_payload"

# Default fragment action allowlist for the default compatibility predicate.
# Actions NOT in this set are candidates for structural incompatibility
# (REPLACE/REPEAL); everything here is fragment-level/move/non-structural for
# the default incompatibility comparison (mirrors EE's
# ``_EE_WHOLE_TARGET_STRUCTURAL_ACTIONS`` complement).
DEFAULT_FRAGMENT_ACTIONS: frozenset[StructuralAction] = frozenset(
    {
        StructuralAction.TEXT_PATCH,
        StructuralAction.HEADING_REPLACE,
        StructuralAction.META,
        StructuralAction.INSERT,
        StructuralAction.RENUMBER,
    }
)

# Whole-target action families classified conservatively for the default
# incompatibility predicate (mirrors EE
# ``_EE_WHOLE_TARGET_DESTRUCTIVE_ACTIONS`` / ``_EE_WHOLE_TARGET_REPLACEMENT_ACTIONS``,
# which themselves mirror UK).
_DEFAULT_WHOLE_TARGET_DESTRUCTIVE_ACTIONS: frozenset[StructuralAction] = frozenset(
    {StructuralAction.REPEAL}
)
_DEFAULT_WHOLE_TARGET_REPLACEMENT_ACTIONS: frozenset[StructuralAction] = frozenset(
    {StructuralAction.REPLACE}
)

# lawvm-regex: prefilter schema validation for typed SameMomentPrecedenceClaim dates; mints no legal state
_ISO_DATE_RE = compile_classifier_regex(
    r"^\d{4}-\d{2}-\d{2}$",
    classifier_id="core.cross_act_same_moment.iso_date",
)
# lawvm-regex: prefilter schema validation for frontend finding prefixes; mints no legal state
_PREFIX_RE = compile_classifier_regex(
    r"^[a-z][a-z0-9_]*$",
    classifier_id="core.cross_act_same_moment.finder_prefix",
)


def same_moment_conflict_finding_kind(finder_kind_prefix: str) -> str:
    """Return the frontend-stamped same-moment conflict finding kind."""
    _validate_finder_kind_prefix(finder_kind_prefix)
    return f"{finder_kind_prefix}_{SAME_MOMENT_CONFLICT_REASON_CODE}_ambiguous"


#─ Carrier dataclasses─────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DetectedSameMomentConflict:
    """A real same-moment cross-act incompatible-payload conflict.

    This is the binding surface a claim validates against: the
    ``(effective_date, affected_target)`` of the collision, the full set of
    conflicting affecting act ids, and the conflicting op ids by act. Derived
    from the detector (see ``detected_same_moment_conflicts_from_ops``), never
    authored by the claimant.
    """

    effective_date: str
    affected_target: str
    conflicting_affecting_acts: tuple[str, ...]
    conflicting_op_ids: tuple[str, ...]
    op_ids_by_act: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SameMomentPrecedenceClaim:
    """Owned determination resolving a same-moment cross-act conflict.

    Fields:

    - ``claim_id``: stable id for the claim row.
    - ``claim_kind``: ``same_moment_precedence`` (validated against
      ``SAME_MOMENT_PRECEDENCE_CLAIM_KIND``).
    - ``statute_id``: the affected statute the conflict lives in (optional; used
      to scope opt-in replay integration to one statute).
    - ``effective_date`` / ``affected_target``: the ``(date, target)`` the
      conflict is at — must match a real detected conflict.
    - ``conflicting_affecting_acts``: the full set of conflicting affecting act
      ids (e.g. ``("ee/act-a/2025", "ee/act-b/2025")``). Must match exactly the
      acts of a real detected conflict.
    - ``winner_affecting_act_id``: WHICH affecting act prevails. Must be one of
      ``conflicting_affecting_acts``.
    - ``winner_op_id``: optionally the specific winning op id (when known and
      bound to the winning act); the validator checks act consistency.
    - ``basis``: a recognized precedence basis (later-enactment, devolution /
      territorial-extent split, express-saving, explicit precedence provision).
    - ``basis_note``: bounded free-form provenance note for the basis (not used
      for any inference).
    - ``claimant`` / ``claim_status``: provenance and lifecycle.
    """

    claim_id: str
    claim_kind: str
    effective_date: str
    affected_target: str
    conflicting_affecting_acts: tuple[str, ...]
    winner_affecting_act_id: str
    basis: str
    statute_id: str = ""
    winner_op_id: str = ""
    basis_note: str = ""
    claimant: str = ""
    claim_status: str = "proposed"


@dataclass(frozen=True, slots=True)
class SameMomentPrecedenceClaimValidation:
    """Deterministic validation result for a same-moment precedence claim."""

    claim_id: str
    effective_date: str
    affected_target: str
    validated: bool
    rule_id: str
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SameMomentPrecedenceClaimRecord:
    """Identity-neutral precedence-claim validation input.

    Core op-level validation maps ``winner_op_id`` to ``winner_record_id``; UK
    effect-level validation maps ``winner_effect_id`` to the same neutral field.
    This keeps the shared proof boundary typed without collapsing phase-specific
    identities.
    """

    claim_id: str
    claim_kind: str
    effective_date: str
    affected_target: str
    conflicting_affecting_acts: tuple[str, ...]
    winner_affecting_act_id: str
    basis: str
    winner_record_id: str = ""


@dataclass(frozen=True, slots=True)
class DetectedSameMomentConflictRecord:
    """Identity-neutral detected-conflict validation input."""

    effective_date: str
    affected_target: str
    conflicting_affecting_acts: tuple[str, ...]
    conflicting_record_ids: tuple[str, ...]
    record_ids_by_act: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SameMomentPrecedenceClaimValidationRecord:
    """Identity-neutral deterministic validation result."""

    claim_id: str
    effective_date: str
    affected_target: str
    validated: bool
    rule_id: str
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)


#─ Internal target key─────────────────────────────────────────────────────


class _SameMomentTargetKey(NamedTuple):
    """Group key for cross-act same-moment conflict detection (no act in key).

    Mirrors the UK ``_SameMomentTargetKey`` and EE ``_EESameMomentTargetKey``:
    the act identifier is intentionally NOT part of the key, because a
    same-moment conflict is a property of the ``(date, target)`` bucket that
    survives regardless of how many acts collide.
    """

    effective_date: str
    affected_target: str


#─ Op accessor helpers─────────────────────────────────────────────────────


def _action_value(op: LegalOperation) -> str:
    """Return the canonical string for an op's action, enum or string either way.

    Routes a collapsed ``TEXT_PATCH`` action (§2.1 O6) back through
    ``legacy_text_action_value`` so a same-moment ``conflicting_ops`` detail keeps
    reporting ``"text_replace"`` / ``"text_repeal"`` byte-identically.
    """
    action = op.action
    if isinstance(action, StructuralAction):
        return legacy_text_action_value(op)
    if hasattr(action, "value"):
        return str(action.value or "")
    return str(action or "")


def _op_effective_date(op: LegalOperation) -> str:
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


def _op_affecting_act_id(op: LegalOperation) -> str:
    """Return the affecting act id provenance string ("" if absent).

    Uses ``OperationSource.statute_id`` as the affecting act id (the amending
    act's id — mirrors EE's ``_ee_op_affecting_act_id`` which itself mirrors
    UK's ``UKEffectRecord.affecting_act_id``).
    """
    if op.source is None:
        return ""
    return str(getattr(op.source, "statute_id", "") or "")


def _op_affected_target(op: LegalOperation) -> str:
    """Return a stable string serialization of the op's structural target path.

    Empty path (statute-level/global) is treated as no structural target — it is
    fragment-level TEXT_REPLACE territory, not a §1.7 cross-act collision. The
    serialized string form is intentionally used (rather than the tuple itself)
    so two ops with the same target string but distinct tuple identities still
    bucket together — this matches the EE detector's shape (which itself mirrors
    the UK detector's ``affected_provisions`` string-key shape).
    """
    if op.target is None:
        return ""
    path = getattr(op.target, "path", ())
    if not path:
        return ""
    return str(path)


def _default_payloads_incompatible(
    left: LegalOperation,
    right: LegalOperation,
    *,
    fragment_actions: frozenset[StructuralAction],
) -> bool:
    """Default conservative compatibility predicate (mirrors EE/UK shape).

    Only whole-target DESTRUCTIVE (REPEAL) and REPLACEMENT (REPLACE) actions are
    treated as incompatible. Fragment-level TEXT_REPLACE, RENUMBER moves,
    HEADING/META ops, and INSERTs at distinct positions can legitimately
    coexist at the same instant and are intentionally NOT flagged here, to
    avoid false ambiguity findings (AGENTS.md §0 — preserve uncertainty, do
    not manufacture findings from coexistence).

    Two REPEALs of the same target from different acts are also NOT
    incompatible — they are redundant destructive effects with the same
    outcome, not order-determining. (The UK detector's verification surface for
    ``repealed`` is a single shared group; flagging repeal+x_repeal would
    manufacture a finding that has no order-decided winner to dispute.)
    """
    left_action = left.action
    right_action = right.action
    # Actions in the fragment allowlist never participate in whole-target
    # incompatibility comparisons — they are non-structural by definition.
    if left_action in fragment_actions or right_action in fragment_actions:
        return False
    left_destructive = left_action in _DEFAULT_WHOLE_TARGET_DESTRUCTIVE_ACTIONS
    right_destructive = right_action in _DEFAULT_WHOLE_TARGET_DESTRUCTIVE_ACTIONS
    left_replacement = left_action in _DEFAULT_WHOLE_TARGET_REPLACEMENT_ACTIONS
    right_replacement = right_action in _DEFAULT_WHOLE_TARGET_REPLACEMENT_ACTIONS
    if not (left_destructive or left_replacement) or not (
        right_destructive or right_replacement
    ):
        return False
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


def _validate_finder_kind_prefix(prefix: str) -> None:
    """Fail loud (AGENTS.md §1.10) on a malformed finder_kind_prefix.

    The prefix stamps the finding ``kind`` and the claim validation/rejection
    ``rule_id``s; an empty or malformed prefix would silently produce
    cross-frontend collisions (e.g. ``"_same_moment_..."`` finding kinds shared
    across frontends defeats the per-frontend audit-trail invariant).
    """
    # lawvm-regex: owning_parser lexical docstring-shape validation; no op minted; no statute text read.
    if not prefix or not _PREFIX_RE.match(str(prefix)):
        raise ValueError(
            f"finder_kind_prefix must be a non-empty lowercase ASCII identifier "
            f"(letters, digits, underscores, leading letter); got {prefix!r}"
        )


#─ Generic record-shaped detection (kernel; UK consumes this)───────────────


def detect_same_moment_conflict_groups_generic(
    records: AbcSequence[R],
    *,
    effective_date_of: Callable[[R], str],
    affecting_act_id_of: Callable[[R], str],
    affected_target_of: Callable[[R], str],
    incompatible_payload_predicate: Callable[[R, R], bool],
) -> dict[_SameMomentTargetKey, list[tuple[R, R]]]:
    """Group same-moment cross-act conflicts for ANY record shape (Wave 0b).

    This is the single shared detection algorithm — divergence #1 of the
    pipeline-unification matrix (``notes/CORE_PIPELINE_UNIFICATION_DESIGN.md``
    §2.1.1). The op-based path (SE/EE/NO via
    :func:`_detect_same_moment_conflict_groups`) and the UK *effect-level* path
    (``uk_legislation/ordering``) both call this with their own accessors +
    incompatibility predicate, so the legal rule (group by
    ``(effective_date, affected_target)``, require ≥2 distinct affecting acts,
    pair distinct-act records whose whole-target payloads collide) lives in ONE
    place. The record TYPE, the accessors, the predicate, and the finding/claim
    serialization stay frontend-supplied (UK records are ``UKEffectRecord``s,
    ordered/detected before lowering; ops are not yet built — see the §2.1
    impedance note and the design doc Wave 0b).

    The grouping iteration order follows ``records`` input order (insertion-
    ordered dict), so a caller's finding *list* order is the record input order.
    Records with no ``affected_target`` or no ``effective_date`` are excluded
    (an undated/global record is not a same-EFFECTIVE-DATE collision —
    bucketing it would manufacture a false ambiguity). Records with an empty
    ``affecting_act_id`` do not participate in the distinct-act count.

    Pairing buckets by affecting act first (``affects_to_ops``) then pairs
    across distinct-act buckets — the op-path shape. The de-duplicated
    participating-record SET a finding is built from is independent of pair
    order, so this is observably equivalent to UK's old index-pair loop on
    findings (which de-dup + sort the participants); see
    ``tests/test_uk_order_ops_parallel_run.py`` for the equality proof.
    """
    target_groups: dict[_SameMomentTargetKey, list[R]] = {}
    for record in records:
        target = affected_target_of(record)
        if not target:
            continue
        effective_date = effective_date_of(record)
        if not effective_date:
            continue
        key = _SameMomentTargetKey(
            effective_date=effective_date,
            affected_target=target,
        )
        target_groups.setdefault(key, []).append(record)

    conflicts: dict[_SameMomentTargetKey, list[tuple[R, R]]] = {}
    for key, group_records in target_groups.items():
        affects_to_records: dict[str, list[R]] = {}
        for record in group_records:
            act_id = affecting_act_id_of(record)
            if not act_id:
                continue
            affects_to_records.setdefault(act_id, []).append(record)
        if len(affects_to_records) < 2:
            continue

        conflicting_pairs: list[tuple[R, R]] = []
        acts_sorted = list(affects_to_records.keys())
        for left_act_pos in range(len(acts_sorted)):
            for right_act_pos in range(left_act_pos + 1, len(acts_sorted)):
                for left in affects_to_records[acts_sorted[left_act_pos]]:
                    for right in affects_to_records[acts_sorted[right_act_pos]]:
                        if incompatible_payload_predicate(left, right):
                            conflicting_pairs.append((left, right))
        if conflicting_pairs:
            conflicts[key] = conflicting_pairs
    return conflicts


#─ Detection-from-ops (binding surface for claims)─────────────────────────


def _detect_same_moment_conflict_groups(
    ops: AbcSequence[LegalOperation],
    *,
    incompatible_payload_predicate: Callable[[LegalOperation, LegalOperation], bool],
    effective_date_of: Optional[Callable[[LegalOperation], str]] = None,
) -> dict[_SameMomentTargetKey, list[tuple[LegalOperation, LegalOperation]]]:
    """Detect same-moment cross-act conflict groups (op shape).

    Thin op-shaped binding of the shared
    :func:`detect_same_moment_conflict_groups_generic`: supplies the op
    accessors (``OperationSource.effective`` / ``.statute_id`` /
    ``LegalAddress.path``). SE/EE/NO consume this; the algorithm body is the
    shared generic one (no op-specific detection logic remains here).

    ``effective_date_of`` overrides the same-EFFECTIVE-DATE bucketing accessor.
    ``None`` (the default for SE/EE/NO/EU/UK) keeps the canonical
    ``_op_effective_date`` (``OperationSource.effective``), so those frontends'
    detection is byte-identical. A frontend whose APPLY-time "same moment" is a
    different source-side date supplies its own accessor (US: ``effective or
    enacted`` — most US amendments are undated-effective and apply AT ENACTMENT,
    so the enactment date is the same-moment key the dry-run's ``(enacted_date,
    statute_id)`` application order already buckets by).
    """
    return detect_same_moment_conflict_groups_generic(
        ops,
        effective_date_of=(
            effective_date_of if effective_date_of is not None else _op_effective_date
        ),
        affecting_act_id_of=_op_affecting_act_id,
        affected_target_of=_op_affected_target,
        incompatible_payload_predicate=incompatible_payload_predicate,
    )


def detected_same_moment_conflicts_from_ops(
    ops: AbcSequence[LegalOperation],
    *,
    incompatible_payload_predicate: Optional[Callable[[LegalOperation, LegalOperation], bool]] = None,
    fragment_action_allowlist: Optional[frozenset[StructuralAction]] = None,
) -> list[DetectedSameMomentConflict]:
    """Return ``DetectedSameMomentConflict`` carriers for a set of ops.

    This is the binding surface a claim validates against: the same detection
    the ambiguity finding uses, exposed as typed carriers (``effective_date``,
    ``affected_target``, the conflicting affecting acts, and the conflicting
    op ids). It never authors a winner.

    Mirrors UK ``conflicts_from_effects`` but accepts ``LegalOperation`` ops
    rather than ``UKEffectRecord`` effects — the EE/most-frontends shape.
    """
    fragment_actions = (
        fragment_action_allowlist
        if fragment_action_allowlist is not None
        else DEFAULT_FRAGMENT_ACTIONS
    )

    def _predicate(left: LegalOperation, right: LegalOperation) -> bool:
        if incompatible_payload_predicate is not None:
            return incompatible_payload_predicate(left, right)
        return _default_payloads_incompatible(
            left, right, fragment_actions=fragment_actions
        )

    conflict_groups = _detect_same_moment_conflict_groups(
        ops, incompatible_payload_predicate=_predicate
    )
    detected: list[DetectedSameMomentConflict] = []
    for key, conflicting_pairs in conflict_groups.items():
        # De-duplicate the conflict participation set by op_id. An op may pair
        # against multiple ops from the other act, but it must appear once.
        conflicting_ops: list[LegalOperation] = []
        seen_op_ids: set[str] = set()
        for pair in conflicting_pairs:
            for op in pair:
                if op.op_id not in seen_op_ids:
                    seen_op_ids.add(op.op_id)
                    conflicting_ops.append(op)

        op_ids_by_act: dict[str, list[str]] = {}
        for op in conflicting_ops:
            act_id = _op_affecting_act_id(op)
            op_ids_by_act.setdefault(act_id, []).append(op.op_id)
        detected.append(
            DetectedSameMomentConflict(
                effective_date=key.effective_date,
                affected_target=key.affected_target,
                conflicting_affecting_acts=tuple(sorted(op_ids_by_act)),
                conflicting_op_ids=tuple(
                    sorted(op.op_id for op in conflicting_ops)
                ),
                op_ids_by_act={
                    act: tuple(ids) for act, ids in op_ids_by_act.items()
                },
            )
        )
    return detected


#─ Claim validation────────────────────────────────────────────────────────


def _conflict_record_matches_claim_record(
    claim: SameMomentPrecedenceClaimRecord,
    conflict: DetectedSameMomentConflictRecord,
) -> bool:
    """Return whether a neutral claim binds a neutral detected conflict."""
    if claim.effective_date != conflict.effective_date:
        return False
    if claim.affected_target.strip() != conflict.affected_target.strip():
        return False
    return set(claim.conflicting_affecting_acts) == set(
        conflict.conflicting_affecting_acts
    )


def _same_moment_precedence_claim_record_from_claim(
    claim: SameMomentPrecedenceClaim,
) -> SameMomentPrecedenceClaimRecord:
    return SameMomentPrecedenceClaimRecord(
        claim_id=claim.claim_id,
        claim_kind=claim.claim_kind,
        effective_date=claim.effective_date,
        affected_target=claim.affected_target,
        conflicting_affecting_acts=claim.conflicting_affecting_acts,
        winner_affecting_act_id=claim.winner_affecting_act_id,
        basis=claim.basis,
        winner_record_id=claim.winner_op_id,
    )


def _detected_same_moment_conflict_record_from_conflict(
    conflict: DetectedSameMomentConflict,
) -> DetectedSameMomentConflictRecord:
    return DetectedSameMomentConflictRecord(
        effective_date=conflict.effective_date,
        affected_target=conflict.affected_target,
        conflicting_affecting_acts=conflict.conflicting_affecting_acts,
        conflicting_record_ids=conflict.conflicting_op_ids,
        record_ids_by_act=conflict.op_ids_by_act,
    )


def validate_same_moment_precedence_claim_record(
    claim: SameMomentPrecedenceClaimRecord,
    *,
    detected_conflicts: Sequence[DetectedSameMomentConflictRecord],
    finder_kind_prefix: str,
    record_id_field: str,
    record_plural_label: str,
) -> SameMomentPrecedenceClaimValidationRecord:
    """Deterministically validate one same-moment claim over neutral identities.

    ``record_id_field`` names the frontend-local winner id field for diagnostics
    (for example ``winner_op_id`` or ``winner_effect_id``). ``record_plural_label``
    names the detected identity set in human-readable rejection text.
    """
    _validate_finder_kind_prefix(finder_kind_prefix)

    claim_validated_rule_id = f"{finder_kind_prefix}_same_moment_precedence_claim_validated"
    claim_rejected_schema_rule_id = (
        f"{finder_kind_prefix}_same_moment_precedence_claim_rejected_schema"
    )
    claim_rejected_conflict_binding_rule_id = (
        f"{finder_kind_prefix}_same_moment_precedence_claim_rejected_conflict_binding"
    )
    claim_rejected_basis_rule_id = (
        f"{finder_kind_prefix}_same_moment_precedence_claim_rejected_basis"
    )

    base = {
        "claim_id": claim.claim_id,
        "effective_date": claim.effective_date,
        "affected_target": claim.affected_target,
    }

    schema_error = _schema_record_error(claim)
    if schema_error:
        return SameMomentPrecedenceClaimValidationRecord(
            validated=False,
            rule_id=claim_rejected_schema_rule_id,
            reason=schema_error,
            detail={
                "claim_kind": claim.claim_kind,
                "conflicting_affecting_acts": list(claim.conflicting_affecting_acts),
            },
            **base,
        )

    matched = next(
        (
            c
            for c in detected_conflicts
            if _conflict_record_matches_claim_record(claim, c)
        ),
        None,
    )
    if matched is None:
        return SameMomentPrecedenceClaimValidationRecord(
            validated=False,
            rule_id=claim_rejected_conflict_binding_rule_id,
            reason=(
                "claim does not match any detected same-moment cross-act "
                "incompatible-payload conflict at this (effective_date, target) "
                "with exactly these acts; the claim may not invent a conflict"
            ),
            detail={
                "claimed_acts": list(claim.conflicting_affecting_acts),
                "detected_conflict_count": len(detected_conflicts),
            },
            **base,
        )
    if claim.winner_record_id:
        if claim.winner_record_id not in matched.conflicting_record_ids:
            return SameMomentPrecedenceClaimValidationRecord(
                validated=False,
                rule_id=claim_rejected_conflict_binding_rule_id,
                reason=(
                    f"claim {record_id_field} is not one of the detected "
                    f"conflict's conflicting {record_plural_label}"
                ),
                detail={record_id_field: claim.winner_record_id},
                **base,
            )
        winner_act_records = matched.record_ids_by_act.get(
            claim.winner_affecting_act_id, ()
        )
        if winner_act_records and claim.winner_record_id not in winner_act_records:
            return SameMomentPrecedenceClaimValidationRecord(
                validated=False,
                rule_id=claim_rejected_conflict_binding_rule_id,
                reason=(
                    f"claim {record_id_field} does not belong to the claimed "
                    "winning affecting act"
                ),
                detail={
                    record_id_field: claim.winner_record_id,
                    "winner_affecting_act_id": claim.winner_affecting_act_id,
                },
                **base,
            )

    if claim.winner_affecting_act_id not in matched.conflicting_affecting_acts:
        return SameMomentPrecedenceClaimValidationRecord(
            validated=False,
            rule_id=claim_rejected_basis_rule_id,
            reason=(
                "claimed winner is not one of the conflicting affecting acts; "
                "the claim may not name an act outside the conflict"
            ),
            detail={
                "winner_affecting_act_id": claim.winner_affecting_act_id,
                "conflicting_affecting_acts": list(
                    matched.conflicting_affecting_acts
                ),
            },
            **base,
        )
    if claim.basis not in _RECOGNIZED_BASES:
        return SameMomentPrecedenceClaimValidationRecord(
            validated=False,
            rule_id=claim_rejected_basis_rule_id,
            reason=f"unrecognized precedence basis {claim.basis!r}",
            detail={"basis": claim.basis},
            **base,
        )

    return SameMomentPrecedenceClaimValidationRecord(
        validated=True,
        rule_id=claim_validated_rule_id,
        reason=(
            "owned same-moment precedence resolution is well-formed, bound to a "
            "real detected cross-act conflict, and names a conflicting act on a "
            "recognized basis"
        ),
        detail={
            "winner_affecting_act_id": claim.winner_affecting_act_id,
            record_id_field: claim.winner_record_id,
            "basis": claim.basis,
            "conflicting_affecting_acts": list(matched.conflicting_affecting_acts),
        },
        **base,
    )


def validate_same_moment_precedence_claim(
    claim: SameMomentPrecedenceClaim,
    *,
    detected_conflicts: Sequence[DetectedSameMomentConflict],
    finder_kind_prefix: str,
) -> SameMomentPrecedenceClaimValidation:
    """Deterministically validate one same-moment precedence claim.

    Mirrors UK ``validate_same_moment_precedence_claim`` but parameterized by
    a ``finder_kind_prefix`` so each frontend (EE/NO/EU/SE/NZ/UK/US) stamps its
    own validation/rejection rule ids.

    Stages, in order:

    1. **Schema** — claim kind, ISO effective date, non-empty target, at least
       two distinct conflicting acts, a named winner, and a basis string are
       all well-formed.
    2. **Conflict binding** — the claim must match a REAL detected same-moment
       incompatible conflict at that ``(date, target)`` with exactly those acts
       (reusing the detection via ``detected_conflicts``). This rejects
       free-form claims not anchored to an actual collision. When the claim
       names a ``winner_op_id``, it must be one of that conflict's conflicting
       ops and belong to the winning act.
    3. **Basis admissibility** — the named winner must be one of the conflicting
       acts and the basis a recognized kind.

    The validator NEVER invents a winner; it only accepts an owned one.
    """
    validation = validate_same_moment_precedence_claim_record(
        _same_moment_precedence_claim_record_from_claim(claim),
        detected_conflicts=tuple(
            _detected_same_moment_conflict_record_from_conflict(c)
            for c in detected_conflicts
        ),
        finder_kind_prefix=finder_kind_prefix,
        record_id_field="winner_op_id",
        record_plural_label="ops",
    )
    return SameMomentPrecedenceClaimValidation(
        claim_id=validation.claim_id,
        effective_date=validation.effective_date,
        affected_target=validation.affected_target,
        validated=validation.validated,
        rule_id=validation.rule_id,
        reason=validation.reason,
        detail=validation.detail,
    )


def _schema_record_error(claim: SameMomentPrecedenceClaimRecord) -> str:
    if claim.claim_kind != SAME_MOMENT_PRECEDENCE_CLAIM_KIND:
        return f"unsupported claim_kind {claim.claim_kind!r}; expected {SAME_MOMENT_PRECEDENCE_CLAIM_KIND!r}"
    if not claim.claim_id:
        return "missing claim_id"
    # lawvm-regex: prefilter schema validation for typed SameMomentPrecedenceClaim dates; mints no legal state
    if not _ISO_DATE_RE.match(str(claim.effective_date)):
        return f"effective_date {claim.effective_date!r} is not an ISO date"
    if not claim.affected_target.strip():
        return "missing affected_target"
    distinct_acts = {a for a in claim.conflicting_affecting_acts if a}
    if len(distinct_acts) < 2:
        return "conflicting_affecting_acts must name at least two distinct acts"
    if not claim.winner_affecting_act_id:
        return "missing winner_affecting_act_id"
    if not claim.basis:
        return "missing basis"
    return ""


def _validated_same_moment_precedence_winners(
    ops: AbcSequence[LegalOperation],
    *,
    finder_kind_prefix: str,
    precedence_claims: Sequence[SameMomentPrecedenceClaim],
    incompatible_payload_predicate: Optional[Callable[[LegalOperation, LegalOperation], bool]],
    fragment_action_allowlist: Optional[frozenset[StructuralAction]],
) -> dict[_SameMomentTargetKey, str]:
    """Index validated precedence claims by conflict key → winning affecting act.

    Mirrors UK ``_validated_same_moment_precedence_winners``. Each claim is
    validated against the REAL detected conflicts (reusing the shared
    detection); only a claim that binds to an actual conflict with exactly
    those acts and a recognized winner/basis contributes a winner. With no
    claims the result is empty and findings are byte-unchanged (unproven).
    """
    if not precedence_claims:
        return {}
    detected = detected_same_moment_conflicts_from_ops(
        ops,
        incompatible_payload_predicate=incompatible_payload_predicate,
        fragment_action_allowlist=fragment_action_allowlist,
    )
    winners: dict[_SameMomentTargetKey, str] = {}
    for claim in precedence_claims:
        validation = validate_same_moment_precedence_claim(
            claim,
            detected_conflicts=detected,
            finder_kind_prefix=finder_kind_prefix,
        )
        if not validation.validated:
            continue
        key = _SameMomentTargetKey(
            effective_date=claim.effective_date,
            affected_target=str(claim.affected_target or "").strip(),
        )
        winners[key] = claim.winner_affecting_act_id
    return winners


#─ Main detector───────────────────────────────────────────────────────────


def detect_cross_act_same_moment_conflicts(
    ops: Iterable[LegalOperation],
    *,
    finder_kind_prefix: str,
    precedence_claims: Sequence[SameMomentPrecedenceClaim] = (),
    incompatible_payload_predicate: Optional[Callable[[LegalOperation, LegalOperation], bool]] = None,
    fragment_action_allowlist: Optional[frozenset[StructuralAction]] = None,
    unproven_resolution_label: str = DEFAULT_UNPROVEN_RESOLUTION_LABEL,
    adjudications_out: Optional[list[CompileAdjudication]] = None,
    lowering_observations_out: Optional[list[dict[str, Any]]] = None,
    effective_date_of: Optional[Callable[[LegalOperation], str]] = None,
) -> list[dict[str, Any]]:
    """Emit blocking §1.7 ambiguity findings for same-moment cross-act conflicts.

    Pre-pass form (runs BEFORE the apply fold, per the UK/EE precedent).
    Detection is ADDITIVE (mirrors EE ``detect_ee_same_moment_cross_act_conflicts``):

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
    emission shape the UK/EE detectors use (EE Pattern A
    ``ee_replay_payload_after_eid``-style at-``op_id=""``).

    Detects cross-act conflicts only (intra-act ordering/scope is its own
    lane). Each frontend's prefix stamps the finding ``kind`` and the
    validation/rejection ``rule_id``s so cross-frontend harmonization does
    not collapse one frontend's audit trail into another's.

    Per AGENTS.md §1.7 (anti-pattern "emit ambiguity unless the precedence
    rule is documented, tested, and justified"): with no validated
    ``precedence_claims`` the finding is ``blocking=True`` and records
    ``resolution: <unproven_resolution_label>`` (default
    ``"sequence_order_unproven"``). When a validated claim binds the conflict,
    the finding is ``blocking=False`` and records
    ``resolution: "resolved_by_claim"``.

    ``effective_date_of`` overrides the same-EFFECTIVE-DATE bucketing accessor
    (default ``None`` => the canonical ``OperationSource.effective``). A frontend
    whose APPLY-time same-moment key is a different source-side date (US: the
    enactment date for undated-effective amendments) supplies its own accessor;
    every existing caller passes ``None`` and is byte-identical.
    """
    _validate_finder_kind_prefix(finder_kind_prefix)

    ops_list = list(ops)
    finding_kind = same_moment_conflict_finding_kind(finder_kind_prefix)

    fragment_actions = (
        fragment_action_allowlist
        if fragment_action_allowlist is not None
        else DEFAULT_FRAGMENT_ACTIONS
    )

    def _predicate(left: LegalOperation, right: LegalOperation) -> bool:
        if incompatible_payload_predicate is not None:
            return incompatible_payload_predicate(left, right)
        return _default_payloads_incompatible(
            left, right, fragment_actions=fragment_actions
        )

    winner_by_conflict = _validated_same_moment_precedence_winners(
        ops_list,
        finder_kind_prefix=finder_kind_prefix,
        precedence_claims=precedence_claims,
        incompatible_payload_predicate=incompatible_payload_predicate,
        fragment_action_allowlist=fragment_action_allowlist,
    )

    conflict_groups = _detect_same_moment_conflict_groups(
        ops_list,
        incompatible_payload_predicate=_predicate,
        effective_date_of=effective_date_of,
    )

    findings: list[dict[str, Any]] = []
    for key, conflicting_pairs in conflict_groups.items():
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
            {_op_affecting_act_id(op) for op in conflicting_ops_unique}
        )
        claimed_winner_act = winner_by_conflict.get(key)
        if claimed_winner_act is not None:
            resolution = RESOLUTION_RESOLVED_BY_CLAIM
            blocking = False
            unproven_reason_suffix = (
                "A validated same-moment precedence claim proves which act "
                "prevails; the materialized winner follows the claim."
            )
        else:
            resolution = unproven_resolution_label
            blocking = True
            unproven_reason_suffix = (
                "The materialized winner is currently chosen by op.sequence "
                "with no precedence rule; this is a §1.7 ambiguity until a "
                "precedence claim proves which act prevails. Apply order is "
                "unchanged; the finding makes the silent pick visible and "
                "strict-rejectable."
            )

        reason = (
            "Two or more affecting acts change the same target at the same "
            "effective date with incompatible whole-target payloads. "
            + unproven_reason_suffix
        )

        detail: dict[str, Any] = {
            "affected_target": key.affected_target,
            "effective_date": key.effective_date,
            "reason_code": SAME_MOMENT_CONFLICT_REASON_CODE,
            "resolution": resolution,
            "conflicting_affecting_acts": tuple(conflicting_acts),
            "conflicting_ops": tuple(
                {
                    "op_id": op.op_id,
                    "affecting_act_id": _op_affecting_act_id(op),
                    "action": _action_value(op),
                    "sequence": op.sequence,
                    "target": str(op.target),
                }
                for op in sorted(
                    conflicting_ops_unique,
                    key=lambda o: (o.sequence, o.op_id),
                )
            ),
        }
        if claimed_winner_act is not None:
            detail["resolved_by_claim_winner_affecting_act_id"] = claimed_winner_act

        record = diagnostic_detail(
            rule_id=finding_kind,
            family="temporal_recovery",
            phase="apply",
            reason=reason,
            blocking=blocking,
            detail=detail,
        )
        findings.append(record)
        if adjudications_out is not None:
            adjudications_out.append(
                CompileAdjudication(
                    kind=finding_kind,
                    message=str(record["reason"]),
                    source_statute="",
                    op_id="",
                    blocking=blocking,
                    phase=str(record.get("phase") or "apply"),
                    detail=record,
                )
            )
        if lowering_observations_out is not None:
            lowering_observations_out.append(dict(record))
    return findings
