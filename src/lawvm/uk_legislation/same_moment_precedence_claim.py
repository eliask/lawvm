"""Owned manual-compilation claim resolving a same-moment cross-act conflict.

When two (or more) effects from DIFFERENT affecting acts share the same
``(effective_date, affected target)`` with incompatible whole-target payloads —
e.g. two distinct substitutions of the whole provision, or a repeal against an
amendment — only one can materialize, and the materialized winner is currently
chosen by ``affecting_act_id`` lexical order in
``ordering._order_uk_effects_for_replay``'s sort key. That silent pick is "legal
conflict resolved by Python accident" (AGENTS.md §1.7): an ambiguity until a
precedence rule proves which act prevails.

``ordering._emit_uk_same_moment_cross_act_conflict_findings`` is the *sensor*: it
emits ``uk_same_moment_cross_act_incompatible_payload_ambiguous`` and records the
pick as ``affecting_act_id_lexical_order_unproven``. This module is the missing
*claim half*: an owned, typed, deterministically-validated determination of WHICH
affecting act's payload prevails at that ``(date, target)``, on a recognized
legal BASIS (later-enactment, devolution / territorial-extent split,
express-saving, explicit precedence provision).

Contract (mirrors ``contingent_commencement_claim`` / the M1 pattern):

- A claim PROPOSES legal meaning (the precedence determination). It does not
  directly mutate state.
- A deterministic validator binds the claim to a REAL detected same-moment
  incompatible conflict at that ``(date, target)`` (reusing the ordering
  detection), with exactly those conflicting acts, rejecting free-form
  overrides. The named winner must be one of the conflicting acts and the basis
  a recognized kind. The validator NEVER invents a winner.
- A validated claim, and only a validated claim, lets ordering resolve the
  conflict by the claimed winner instead of ``affecting_act_id`` lexical order;
  the finding then records ``resolved_by_claim`` instead of
  ``affecting_act_id_lexical_order_unproven``.
- With NO claim authored, ordering is byte-unchanged: the string-order default
  and the unproven finding stand exactly as today.

Witness (verified against the farchive baseline): SI ``2000/1043`` reg. 11(3) is
substituted at ``2005-07-16`` by BOTH ``uksi/2005/894`` and ``wsi/2005/1806``
(a UK SI and a Welsh SI). A devolution / territorial-extent split is the kind of
basis an owned claim records to resolve which instrument prevails.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Sequence

from lawvm.core.cross_act_same_moment import (
    BASIS_DEVOLUTION_TERRITORIAL_EXTENT_SPLIT,
    BASIS_EXPRESS_SAVING,
    BASIS_EXPLICIT_PRECEDENCE_PROVISION,
    BASIS_LATER_ENACTMENT,
    DetectedSameMomentConflictRecord,
    SAME_MOMENT_PRECEDENCE_CLAIM_KIND,
    SameMomentPrecedenceClaimRecord,
    validate_same_moment_precedence_claim_record,
)
from lawvm.uk_legislation.phase_discipline import UK_PHASE_EFFECT_METADATA_FRONTEND

__all__ = [
    "BASIS_DEVOLUTION_TERRITORIAL_EXTENT_SPLIT",
    "BASIS_EXPRESS_SAVING",
    "BASIS_EXPLICIT_PRECEDENCE_PROVISION",
    "BASIS_LATER_ENACTMENT",
    "CLAIM_REJECTED_BASIS_RULE_ID",
    "CLAIM_REJECTED_CONFLICT_BINDING_RULE_ID",
    "CLAIM_REJECTED_SCHEMA_RULE_ID",
    "CLAIM_VALIDATED_RULE_ID",
    "DetectedSameMomentConflict",
    "RESOLUTION_LEXICAL_ORDER_UNPROVEN",
    "SAME_MOMENT_PRECEDENCE_CLAIM_KIND",
    "SAME_MOMENT_PRECEDENCE_CLAIM_TEMPLATE_RULE_ID",
    "SAME_MOMENT_PRECEDENCE_RESOLUTION_PROOF_SEMANTIC",
    "SameMomentPrecedenceClaim",
    "SameMomentPrecedenceClaimValidation",
    "claim_from_dict",
    "validate_same_moment_precedence_claim",
]

# Manual-frontier rule id this claim family advertises. Registered in
# ``UK_MANUAL_CLAIM_TEMPLATE_RULE_IDS`` so the conflict shape advertises an owned
# claim template, consistent with the ordering finding it resolves.
SAME_MOMENT_PRECEDENCE_CLAIM_TEMPLATE_RULE_ID = (
    "uk_manual_frontier_same_moment_cross_act_precedence_resolution_candidate"
)

# Proof-semantic id for the claim's owned determination.
SAME_MOMENT_PRECEDENCE_RESOLUTION_PROOF_SEMANTIC = (
    "same_moment_cross_act_precedence_resolution"
)

# Validator rule ids (named per §0/§7: every owned determination is traceable).
CLAIM_VALIDATED_RULE_ID = "uk_same_moment_precedence_claim_validated"
CLAIM_REJECTED_SCHEMA_RULE_ID = "uk_same_moment_precedence_claim_rejected_schema"
CLAIM_REJECTED_CONFLICT_BINDING_RULE_ID = (
    "uk_same_moment_precedence_claim_rejected_conflict_binding"
)
CLAIM_REJECTED_BASIS_RULE_ID = "uk_same_moment_precedence_claim_rejected_basis"

# The ordering finding's resolution value when a validated claim resolves it.
# The default (no-claim) value emitted by the ordering finding.
RESOLUTION_LEXICAL_ORDER_UNPROVEN = "affecting_act_id_lexical_order_unproven"


@dataclass(frozen=True, slots=True)
class SameMomentPrecedenceClaim:
    """Owned determination resolving a same-moment cross-act conflict.

    Fields:

    - ``claim_id``: stable id for the claim row.
    - ``claim_kind``: ``same_moment_precedence``.
    - ``statute_id``: the affected statute the conflict lives in (optional; used
      to scope opt-in replay integration to one statute).
    - ``effective_date`` / ``affected_target``: the ``(date, target)`` the
      conflict is at — must match a real detected conflict.
    - ``conflicting_affecting_acts``: the full set of conflicting affecting act
      ids (e.g. ``("uksi/2005/894", "wsi/2005/1806")``). Must match exactly the
      acts of a real detected conflict.
    - ``winner_affecting_act_id``: WHICH affecting act prevails. Must be one of
      ``conflicting_affecting_acts``.
    - ``winner_effect_id``: optionally the specific winning effect id (when known
      and bound to the winning act); the validator checks act consistency.
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
    winner_effect_id: str = ""
    basis_note: str = ""
    claimant: str = ""
    claim_status: str = "proposed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "claim_kind": self.claim_kind,
            "statute_id": self.statute_id,
            "effective_date": self.effective_date,
            "affected_target": self.affected_target,
            "conflicting_affecting_acts": list(self.conflicting_affecting_acts),
            "winner_affecting_act_id": self.winner_affecting_act_id,
            "winner_effect_id": self.winner_effect_id,
            "basis": self.basis,
            "basis_note": self.basis_note,
            "claimant": self.claimant,
            "claim_status": self.claim_status,
        }


@dataclass(frozen=True, slots=True)
class SameMomentPrecedenceClaimValidation:
    """Deterministic validation result for a same-moment precedence claim."""

    claim_id: str
    effective_date: str
    affected_target: str
    validated: bool
    rule_id: str
    proof_semantic: str
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "claim_id": self.claim_id,
            "effective_date": self.effective_date,
            "affected_target": self.affected_target,
            "validated": self.validated,
            "rule_id": self.rule_id,
            "proof_semantic": self.proof_semantic,
            "reason": self.reason,
            "owner_phase": UK_PHASE_EFFECT_METADATA_FRONTEND,
        }
        for key in sorted(self.detail):
            row.setdefault(key, self.detail[key])
        return row


@dataclass(frozen=True, slots=True)
class DetectedSameMomentConflict:
    """A real same-moment cross-act incompatible-payload conflict.

    This is the binding surface the validator matches a claim against: the
    ``(effective_date, affected_target)`` of the collision, the full set of
    conflicting affecting act ids, and the conflicting effect ids by act. It is
    derived from the ordering detection (see ``conflicts_from_effects``), never
    authored by the claimant.
    """

    effective_date: str
    affected_target: str
    conflicting_affecting_acts: tuple[str, ...]
    conflicting_effect_ids: tuple[str, ...]
    effect_ids_by_act: dict[str, tuple[str, ...]] = field(default_factory=dict)


def claim_from_dict(row: Mapping[str, object]) -> SameMomentPrecedenceClaim:
    """Build a claim carrier from a mapping row.

    This is a plain deserializer; it does not validate — call
    ``validate_same_moment_precedence_claim``.
    """
    get = row.get
    acts_raw = get("conflicting_affecting_acts")
    if isinstance(acts_raw, str):
        acts = (acts_raw,)
    elif isinstance(acts_raw, (list, tuple)):
        acts = tuple(str(act) for act in acts_raw)
    else:
        acts = ()
    return SameMomentPrecedenceClaim(
        claim_id=str(get("claim_id") or ""),
        claim_kind=str(get("claim_kind") or ""),
        statute_id=str(get("statute_id") or ""),
        effective_date=str(get("effective_date") or ""),
        affected_target=str(get("affected_target") or ""),
        conflicting_affecting_acts=acts,
        winner_affecting_act_id=str(get("winner_affecting_act_id") or ""),
        winner_effect_id=str(get("winner_effect_id") or ""),
        basis=str(get("basis") or ""),
        basis_note=str(get("basis_note") or ""),
        claimant=str(get("claimant") or ""),
        claim_status=str(get("claim_status") or "proposed"),
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
        winner_record_id=claim.winner_effect_id,
    )


def _detected_same_moment_conflict_record_from_conflict(
    conflict: DetectedSameMomentConflict,
) -> DetectedSameMomentConflictRecord:
    return DetectedSameMomentConflictRecord(
        effective_date=conflict.effective_date,
        affected_target=conflict.affected_target,
        conflicting_affecting_acts=conflict.conflicting_affecting_acts,
        conflicting_record_ids=conflict.conflicting_effect_ids,
        record_ids_by_act=conflict.effect_ids_by_act,
    )


def validate_same_moment_precedence_claim(
    claim: SameMomentPrecedenceClaim,
    *,
    detected_conflicts: Sequence[DetectedSameMomentConflict],
) -> SameMomentPrecedenceClaimValidation:
    """Deterministically validate one same-moment precedence claim.

    Stages, in order:

    1. **Schema** — claim kind, ISO effective date, non-empty target, at least
       two distinct conflicting acts, a named winner, and a basis string are all
       well-formed.
    2. **Conflict binding** — the claim must match a REAL detected same-moment
       incompatible conflict at that ``(date, target)`` with exactly those acts
       (reusing the ordering detection via ``detected_conflicts``). This rejects
       free-form claims not anchored to an actual collision. When the claim names
       a ``winner_effect_id``, it must be one of that conflict's conflicting
       effects and belong to the winning act.
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
        finder_kind_prefix="uk",
        record_id_field="winner_effect_id",
        record_plural_label="effects",
    )
    return SameMomentPrecedenceClaimValidation(
        claim_id=validation.claim_id,
        effective_date=validation.effective_date,
        affected_target=validation.affected_target,
        proof_semantic=SAME_MOMENT_PRECEDENCE_RESOLUTION_PROOF_SEMANTIC,
        validated=validation.validated,
        rule_id=validation.rule_id,
        reason=validation.reason,
        detail=validation.detail,
    )
