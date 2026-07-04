"""Author-owned oracle_suspect claim: a feed-vs-consolidation contradiction.

Task #211's deferred **D3** class (the oracle-defect lane; mirrors the EE / NO
``oracle_suspect`` discipline). Concrete shape::

    ukpga/1949/97 s.80(5) / s.80(6) <- uksi/2005/1082 Sch.5 para.6 / Sch.6
        feed:    repealed, Applied="true", commenced 2005-04-26
        oracle:  retains BOTH subsections LIVE with no repeal annotation

The official effects feed (a legislation.gov.uk product) marks the provision
repealed and APPLIED with a real commenced date, yet the SAME publisher's
consolidation (the oracle) retains it live and UNANNOTATED. Replay correctly
applied the feed-authorised repeal, so the resulting oracle-only presence
residual is a **publisher self-contradiction** — an oracle defect, not a replay
bug. This is exactly the ``authoritative oracle ≠ correct`` case the honesty
regime types as ``oracle_suspect``: a first-class, high-value finding, NOT a
divergence to repair toward.

Contract (mirrors the house style — ``source_feed_reconciliation_claim`` /
``deixis_application_claim``):

- A claim PROPOSES that named eIds are oracle-defective. The safe effect is a
  COMPARISON-ONLY, monotone presence drop: a validated claim's eIds are removed
  from the oracle side of the replay-vs-oracle compare set ONLY where replay
  does not carry them (feed-repealed ⇒ replay dropped them). It NEVER forces
  replay to re-add the provision and NEVER touches ``compiled`` ops or
  materialized text — replay stays byte-identical.
- A deterministic validator binds the claim to REALITY, never inferring: it
  requires (a) a compiled REPEAL op whose target eId matches the claimed eId,
  (b) the bound feed effect to be a repeal with ``applied`` true, and (c) the
  oracle to STILL carry the eId live (the contradiction). If any leg fails the
  claim is rejected and contributes no presence drop.
- With NO claim authored the presence set is empty and every comparison is
  byte-unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from lawvm.uk_legislation.phase_discipline import UK_PHASE_EFFECT_METADATA_FRONTEND

# ── Claim kind + rule ids ────────────────────────────────────────────────────
ORACLE_SUSPECT_CLAIM_KIND = "oracle_suspect_feed_repeal"

CLAIM_VALIDATED_RULE_ID = "uk_oracle_suspect_claim_validated"
CLAIM_REJECTED_SCHEMA_RULE_ID = "uk_oracle_suspect_claim_rejected_schema"
CLAIM_REJECTED_NO_REPEAL_OP_RULE_ID = "uk_oracle_suspect_claim_rejected_no_repeal_op"
CLAIM_REJECTED_FEED_NOT_APPLIED_RULE_ID = (
    "uk_oracle_suspect_claim_rejected_feed_not_applied"
)
CLAIM_REJECTED_ORACLE_ABSENT_RULE_ID = (
    "uk_oracle_suspect_claim_rejected_oracle_absent"
)


@dataclass(frozen=True, slots=True)
class OracleSuspectClaim:
    """Owned determination that named eIds are oracle-defective (D3).

    Fields:

    - ``claim_id``: stable id for the claim row.
    - ``claim_kind``: ``oracle_suspect_feed_repeal``.
    - ``statute_id`` / ``effect_id``: the affected statute and the bound feed
      repeal effect (e.g. ``ukpga/1949/97`` <- ``uksi/2005/1082`` Sch.5 para.6).
    - ``suspect_eids``: the oracle eIds the claim types oracle_suspect (the
      compare-surface eIds, e.g. ``section-80-5`` / ``section-80-6``).
    - ``affected_target``: human-readable feed target (``s. 80(5)/(6)``).
    - ``rationale``: free-form provenance note (not validated).
    - ``claimant`` / ``claim_status``: provenance and lifecycle.
    """

    claim_id: str
    claim_kind: str
    statute_id: str
    effect_id: str
    suspect_eids: tuple[str, ...]
    affected_target: str = ""
    rationale: str = ""
    claimant: str = ""
    claim_status: str = "proposed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "claim_kind": self.claim_kind,
            "statute_id": self.statute_id,
            "effect_id": self.effect_id,
            "suspect_eids": list(self.suspect_eids),
            "affected_target": self.affected_target,
            "rationale": self.rationale,
            "claimant": self.claimant,
            "claim_status": self.claim_status,
        }


@dataclass(frozen=True, slots=True)
class OracleSuspectClaimValidation:
    """Deterministic validation result for an oracle_suspect claim."""

    claim_id: str
    statute_id: str
    effect_id: str
    validated: bool
    rule_id: str
    reason: str
    suspect_eids: tuple[str, ...] = ()
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "claim_id": self.claim_id,
            "statute_id": self.statute_id,
            "effect_id": self.effect_id,
            "validated": self.validated,
            "rule_id": self.rule_id,
            "reason": self.reason,
            "suspect_eids": list(self.suspect_eids),
            "owner_phase": UK_PHASE_EFFECT_METADATA_FRONTEND,
        }
        for key in sorted(self.detail):
            row.setdefault(key, self.detail[key])
        return row


def claim_from_dict(row: Any) -> OracleSuspectClaim:
    """Build a claim carrier from a mapping row (plain deserializer, no validation)."""
    get = row.get
    raw_eids = get("suspect_eids") or ()
    suspect_eids = tuple(str(e) for e in raw_eids if str(e or "").strip())
    return OracleSuspectClaim(
        claim_id=str(get("claim_id") or ""),
        claim_kind=str(get("claim_kind") or ""),
        statute_id=str(get("statute_id") or ""),
        effect_id=str(get("effect_id") or ""),
        suspect_eids=suspect_eids,
        affected_target=str(get("affected_target") or ""),
        rationale=str(get("rationale") or ""),
        claimant=str(get("claimant") or ""),
        claim_status=str(get("claim_status") or "proposed"),
    )


def _normalize_eid(eid: str) -> str:
    """Loose compare-eid normalizer: lowercase, hyphen/underscore/colon → hyphen."""
    text = str(eid or "").strip().lower()
    for ch in ("_", ":", "/"):
        text = text.replace(ch, "-")
    return "-".join(part for part in text.split("-") if part)


def validate_oracle_suspect_claim(
    claim: OracleSuspectClaim,
    *,
    statute_id: str,
    repeal_op_target_eids: Iterable[str],
    repeal_effect_ids_applied: Iterable[str],
    oracle_eids: Iterable[str],
) -> OracleSuspectClaimValidation:
    """Bind an oracle_suspect claim to reality (never inferring the decision).

    The claim is validated iff, for EVERY claimed suspect eId:

    * a compiled REPEAL op targets it (replay applied the feed-authorised
      repeal — ``repeal_op_target_eids``);
    * the bound feed effect is an APPLIED repeal (``effect_id`` ∈
      ``repeal_effect_ids_applied``); and
    * the oracle STILL carries the eId live (``oracle_eids``) — the publisher
      self-contradiction.

    Any failing leg rejects the whole claim (no partial credit); the returned
    ``suspect_eids`` on a validated claim are the normalized eIds to drop from
    the oracle compare side.
    """
    if (
        not claim.claim_id
        or claim.claim_kind != ORACLE_SUSPECT_CLAIM_KIND
        or claim.statute_id != statute_id
        or not claim.effect_id
        or not claim.suspect_eids
    ):
        return OracleSuspectClaimValidation(
            claim_id=claim.claim_id,
            statute_id=statute_id,
            effect_id=claim.effect_id,
            validated=False,
            rule_id=CLAIM_REJECTED_SCHEMA_RULE_ID,
            reason=(
                "oracle_suspect claim is missing a required field, names a "
                "different statute, or carries no suspect eIds"
            ),
        )

    applied_effect_ids = {str(e) for e in repeal_effect_ids_applied if str(e or "")}
    if claim.effect_id not in applied_effect_ids:
        return OracleSuspectClaimValidation(
            claim_id=claim.claim_id,
            statute_id=statute_id,
            effect_id=claim.effect_id,
            validated=False,
            rule_id=CLAIM_REJECTED_FEED_NOT_APPLIED_RULE_ID,
            reason=(
                "bound effect is not an APPLIED feed repeal; oracle_suspect "
                "requires a feed-authorised repeal replay actually applied"
            ),
        )

    repeal_norm = {_normalize_eid(e) for e in repeal_op_target_eids if str(e or "")}
    oracle_norm = {_normalize_eid(e) for e in oracle_eids if str(e or "")}
    suspect_norm = tuple(_normalize_eid(e) for e in claim.suspect_eids)

    missing_repeal = [e for e in suspect_norm if e not in repeal_norm]
    if missing_repeal:
        return OracleSuspectClaimValidation(
            claim_id=claim.claim_id,
            statute_id=statute_id,
            effect_id=claim.effect_id,
            validated=False,
            rule_id=CLAIM_REJECTED_NO_REPEAL_OP_RULE_ID,
            reason=(
                "no compiled REPEAL op targets a claimed suspect eId; replay did "
                "not apply the repeal, so there is no oracle contradiction to type"
            ),
            detail={"eids_without_repeal_op": sorted(missing_repeal)},
        )

    oracle_present = [e for e in suspect_norm if e not in oracle_norm]
    if oracle_present:
        return OracleSuspectClaimValidation(
            claim_id=claim.claim_id,
            statute_id=statute_id,
            effect_id=claim.effect_id,
            validated=False,
            rule_id=CLAIM_REJECTED_ORACLE_ABSENT_RULE_ID,
            reason=(
                "the oracle does NOT retain a claimed suspect eId live, so there "
                "is no publisher self-contradiction (nothing to type oracle_suspect)"
            ),
            detail={"eids_absent_from_oracle": sorted(oracle_present)},
        )

    return OracleSuspectClaimValidation(
        claim_id=claim.claim_id,
        statute_id=statute_id,
        effect_id=claim.effect_id,
        validated=True,
        rule_id=CLAIM_VALIDATED_RULE_ID,
        reason=(
            "feed marks the provision repealed + Applied with a real commenced "
            "date, replay applied it, yet the oracle retains it live unannotated "
            "— a publisher self-contradiction typed oracle_suspect (no replay "
            "forced; comparison-only presence drop)"
        ),
        suspect_eids=suspect_norm,
    )


def validated_oracle_suspect_eids(
    claims: Iterable[OracleSuspectClaim],
    *,
    statute_id: str,
    repeal_op_target_eids: Iterable[str],
    repeal_effect_ids_applied: Iterable[str],
    oracle_eids: Iterable[str],
    validations_out: Optional[list[dict[str, Any]]] = None,
) -> set[str]:
    """Return the union of normalized suspect eIds across VALIDATED claims.

    Each claim is validated independently; rejected claims contribute nothing.
    When ``validations_out`` is supplied every validation record is appended for
    audit (validated and rejected alike).
    """
    repeal_targets = tuple(repeal_op_target_eids)
    applied_ids = tuple(repeal_effect_ids_applied)
    oracle = tuple(oracle_eids)
    out: set[str] = set()
    for claim in claims:
        validation = validate_oracle_suspect_claim(
            claim,
            statute_id=statute_id,
            repeal_op_target_eids=repeal_targets,
            repeal_effect_ids_applied=applied_ids,
            oracle_eids=oracle,
        )
        if validations_out is not None:
            validations_out.append(validation.to_dict())
        if validation.validated:
            out.update(validation.suspect_eids)
    return out
