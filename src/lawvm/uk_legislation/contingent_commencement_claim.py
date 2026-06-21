"""Owned manual-compilation claim for UK contingent/conditional-temporal repeal.

A family of UK provisions repeal themselves *contingently* on an out-of-band
commencement trigger, e.g.::

    "[provision] is repealed at the end of 2026 if [it] has not been brought
     into force [by then]."

The result depends on a fact replay cannot compute from the affected statute's
source alone: did the named provision commence before the deadline? This is the
canonical manual-compilation frontier (AGENTS.md §2.1) and the family MOST prone
to forbidden over-application — applying the repeal when the trigger did NOT in
fact fire over-repeals live legal state.

Today LawVM has a *sensor* for this shape (``source_adjudication``'s
``conditional_temporal_repeal_unsupported`` classification, and the
``prospective_*`` witnesses), which keeps the effect on the manual frontier and
never executes it. This module is the missing *claim half*: an owned, typed,
deterministically-validated determination of the trigger so that — and only
when — such a claim exists, replay can gate the conditional repeal to the
compiled point-in-time.

Contract (mirrors ``notes/MANUAL_COMPILATION_CLAIMS.md``):

- A claim PROPOSES legal meaning (the trigger resolution). It does not directly
  mutate state.
- A deterministic validator binds the claim's bounded source snippet to a REAL
  conditional-temporal-repeal effect (rejecting free-form overrides), and checks
  the witness shape of the owned resolution.
- A validated claim, and only a validated claim, gates the resolved effect to
  the PIT: the conditional repeal applies iff the PIT date is past the resolved
  (non-)commencement deadline AND the trigger in fact commenced.
- With NO claim authored, replay is byte-unchanged: the effect stays on the
  manual frontier exactly as today.

The owned determination is one of two witnessed resolutions:

- ``commenced``: the triggered provision was brought into force, witnessed by a
  commencement SI id and the commenced-by date. The conditional repeal therefore
  does NOT fire (the provision commenced, so the "if not brought into force"
  condition is false) — unless the source phrasing is the inverse "repealed
  unless brought into force", captured by ``repeal_fires_on``.
- ``did_not_commence``: the triggered provision was not brought into force by the
  deadline. The conditional repeal therefore fires from the deadline date.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from lawvm.uk_legislation.phase_discipline import UK_PHASE_EFFECT_METADATA_FRONTEND
from lawvm.uk_legislation.source_adjudication import (
    _looks_like_conditional_temporal_repeal_source,
)

# ── Claim kind + rule ids ────────────────────────────────────────────────────
CONTINGENT_COMMENCEMENT_CLAIM_KIND = "contingent_commencement"
CONDITIONAL_TEMPORAL_REPEAL_CLAIM_KIND = "conditional_temporal_repeal"
_CLAIM_KINDS = frozenset(
    {CONTINGENT_COMMENCEMENT_CLAIM_KIND, CONDITIONAL_TEMPORAL_REPEAL_CLAIM_KIND}
)

# Manual-frontier rule id this claim resolves. Registered in
# ``UK_MANUAL_CLAIM_TEMPLATE_RULE_IDS`` so the family advertises an owned claim
# template, and consistent with the existing
# ``conditional_temporal_repeal_unsupported`` source-adjudication classification.
CONTINGENT_COMMENCEMENT_CLAIM_TEMPLATE_RULE_ID = (
    "uk_manual_frontier_conditional_temporal_repeal_resolution_candidate"
)

# Proof-semantic id for the claim's owned determination.
CONTINGENT_COMMENCEMENT_RESOLUTION_PROOF_SEMANTIC = (
    "contingent_commencement_resolution"
)

# Validator rule ids (named per §0/§7: every owned determination is traceable).
CLAIM_VALIDATED_RULE_ID = "uk_contingent_commencement_claim_validated"
CLAIM_REJECTED_SCHEMA_RULE_ID = "uk_contingent_commencement_claim_rejected_schema"
CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID = (
    "uk_contingent_commencement_claim_rejected_source_mismatch"
)
CLAIM_REJECTED_WITNESS_RULE_ID = (
    "uk_contingent_commencement_claim_rejected_witness"
)

# Resolution kinds.
RESOLUTION_COMMENCED = "commenced"
RESOLUTION_DID_NOT_COMMENCE = "did_not_commence"
_RESOLUTION_KINDS = frozenset({RESOLUTION_COMMENCED, RESOLUTION_DID_NOT_COMMENCE})

# Which resolution makes the conditional repeal fire. The default source shape is
# "repealed ... if [it] has not been brought into force": the repeal fires on a
# ``did_not_commence`` resolution. The validator only accepts these two owned
# directions; it never infers them.
REPEAL_FIRES_ON_DID_NOT_COMMENCE = RESOLUTION_DID_NOT_COMMENCE
REPEAL_FIRES_ON_COMMENCED = RESOLUTION_COMMENCED
_REPEAL_FIRES_ON_VALUES = frozenset(
    {REPEAL_FIRES_ON_DID_NOT_COMMENCE, REPEAL_FIRES_ON_COMMENCED}
)

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True, slots=True)
class ContingentCommencementClaim:
    """Owned determination resolving a UK conditional-temporal-repeal effect.

    Fields:

    - ``claim_id``: stable id for the claim row.
    - ``claim_kind``: ``contingent_commencement`` / ``conditional_temporal_repeal``.
    - ``statute_id`` / ``effect_id``: the affected statute and the bound effect.
    - ``trigger_id``: the out-of-band commencement trigger being resolved (e.g.
      the affecting provision whose in-force state the repeal is contingent on).
    - ``deadline_date``: ISO date the contingency is measured at (the "end of
      YEAR" deadline). The repeal cannot apply before this date.
    - ``source_snippet``: bounded quote of the conditional-repeal source text the
      claim binds to. The validator rejects the claim if this snippet does not
      match a real conditional-temporal-repeal shape.
    - ``resolution``: ``commenced`` or ``did_not_commence`` — the owned finding.
    - ``repeal_fires_on``: which resolution makes the repeal fire (defaults to the
      canonical "fires when it did NOT commence").
    - ``witness_si_id``: commencement SI that brought the trigger into force
      (required when ``resolution == commenced``).
    - ``commenced_by_date``: ISO date the trigger commenced (required when
      ``resolution == commenced``).
    - ``claimant`` / ``claim_status``: provenance and lifecycle.
    """

    claim_id: str
    claim_kind: str
    statute_id: str
    effect_id: str
    trigger_id: str
    deadline_date: str
    source_snippet: str
    resolution: str
    repeal_fires_on: str = REPEAL_FIRES_ON_DID_NOT_COMMENCE
    witness_si_id: str = ""
    commenced_by_date: str = ""
    claimant: str = ""
    claim_status: str = "proposed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "claim_kind": self.claim_kind,
            "statute_id": self.statute_id,
            "effect_id": self.effect_id,
            "trigger_id": self.trigger_id,
            "deadline_date": self.deadline_date,
            "source_snippet": self.source_snippet,
            "resolution": self.resolution,
            "repeal_fires_on": self.repeal_fires_on,
            "witness_si_id": self.witness_si_id,
            "commenced_by_date": self.commenced_by_date,
            "claimant": self.claimant,
            "claim_status": self.claim_status,
        }


@dataclass(frozen=True, slots=True)
class ContingentCommencementClaimValidation:
    """Deterministic validation result for a contingent-commencement claim."""

    claim_id: str
    statute_id: str
    effect_id: str
    validated: bool
    rule_id: str
    proof_semantic: str
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "claim_id": self.claim_id,
            "statute_id": self.statute_id,
            "effect_id": self.effect_id,
            "validated": self.validated,
            "rule_id": self.rule_id,
            "proof_semantic": self.proof_semantic,
            "reason": self.reason,
            "owner_phase": UK_PHASE_EFFECT_METADATA_FRONTEND,
        }
        for key in sorted(self.detail):
            row.setdefault(key, self.detail[key])
        return row


def _is_iso_date(value: str) -> bool:
    return bool(_ISO_DATE_RE.match(str(value or "")))


def claim_from_dict(row: Any) -> ContingentCommencementClaim:
    """Build a claim carrier from a mapping row.

    Missing optional fields default to empty/``proposed``. This is a plain
    deserializer; it does not validate — call ``validate_contingent_commencement_claim``.
    """
    get = row.get
    return ContingentCommencementClaim(
        claim_id=str(get("claim_id") or ""),
        claim_kind=str(get("claim_kind") or ""),
        statute_id=str(get("statute_id") or ""),
        effect_id=str(get("effect_id") or ""),
        trigger_id=str(get("trigger_id") or ""),
        deadline_date=str(get("deadline_date") or ""),
        source_snippet=str(get("source_snippet") or ""),
        resolution=str(get("resolution") or ""),
        repeal_fires_on=str(get("repeal_fires_on") or REPEAL_FIRES_ON_DID_NOT_COMMENCE),
        witness_si_id=str(get("witness_si_id") or ""),
        commenced_by_date=str(get("commenced_by_date") or ""),
        claimant=str(get("claimant") or ""),
        claim_status=str(get("claim_status") or "proposed"),
    )


def _effect_conditional_repeal_source_text(
    effect: Any, extracted_source_text: Optional[str] = None
) -> str:
    """Best-effort source-text surface for an effect's conditional-repeal binding.

    The conditional-temporal-repeal shape can live in the effect type (the feed's
    verb phrase, e.g. "repealed at the end of 2026 if ...") or in an attached
    source snippet. We concatenate the available surfaces so the binding check is
    robust to which surface carries the phrasing; the classifier itself is the
    arbiter of whether the shape is real.

    On REAL feed effects the effect attributes (``source_text`` / ``raw_text`` /
    ``comments``) are empty — the instruction prose lives in the extracted
    affecting XML, which the replay pipeline passes in as ``extracted_source_text``
    (the same surface the manual-frontier classifier binds). When supplied it is
    concatenated first; the effect attributes remain as a fallback so synthetic
    unit-fixture effects (which carry the prose in ``comments``) keep binding.
    """
    parts: list[str] = []
    if extracted_source_text:
        parts.append(str(extracted_source_text))
    for attr in ("source_text", "raw_text", "effect_type", "comments"):
        value = getattr(effect, attr, "") or ""
        if value:
            parts.append(str(value))
    return " ".join(parts)


def validate_contingent_commencement_claim(
    claim: ContingentCommencementClaim,
    *,
    effect: Optional[Any] = None,
    extracted_source_text: Optional[str] = None,
) -> ContingentCommencementClaimValidation:
    """Deterministically validate one contingent-commencement claim.

    Checks, in order:

    1. **Schema** — claim kind, resolution, repeal direction, deadline date, and
       trigger id are well-formed; ``commenced`` resolutions carry a witness SI
       and commenced-by date.
    2. **Source binding** — the claim's ``source_snippet`` matches a real
       conditional-temporal-repeal shape (reusing the source-adjudication
       classifier), and — when an ``effect`` is supplied — the bound effect's
       source surface ALSO carries that shape and its ids match the claim. This
       rejects free-form overrides that are not anchored to a real conditional
       repeal.
    3. **Witness** — a ``commenced`` resolution's ``commenced_by_date`` is not
       after the deadline (it actually commenced within the contingency window);
       a ``did_not_commence`` resolution carries no commencement witness.

    The validator never *infers* a resolution; it only accepts an owned one.
    """
    base = {
        "claim_id": claim.claim_id,
        "statute_id": claim.statute_id,
        "effect_id": claim.effect_id,
        "proof_semantic": CONTINGENT_COMMENCEMENT_RESOLUTION_PROOF_SEMANTIC,
    }

    # 1. Schema.
    schema_error = _schema_error(claim)
    if schema_error:
        return ContingentCommencementClaimValidation(
            validated=False,
            rule_id=CLAIM_REJECTED_SCHEMA_RULE_ID,
            reason=schema_error,
            detail={"claim_kind": claim.claim_kind, "resolution": claim.resolution},
            **base,
        )

    # 2. Source binding.
    if not _looks_like_conditional_temporal_repeal_source(claim.source_snippet):
        return ContingentCommencementClaimValidation(
            validated=False,
            rule_id=CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID,
            reason=(
                "claim source_snippet does not match a conditional-temporal-repeal "
                "shape; the claim may not override a free-form effect"
            ),
            detail={"source_snippet": claim.source_snippet[:240]},
            **base,
        )
    if effect is not None:
        effect_id = str(getattr(effect, "effect_id", "") or "")
        if effect_id and claim.effect_id and effect_id != claim.effect_id:
            return ContingentCommencementClaimValidation(
                validated=False,
                rule_id=CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID,
                reason=(
                    "claim effect_id does not match the bound effect; claim must "
                    "bind to the real conditional-repeal effect"
                ),
                detail={"bound_effect_id": effect_id},
                **base,
            )
        effect_source = _effect_conditional_repeal_source_text(
            effect, extracted_source_text
        )
        if not _looks_like_conditional_temporal_repeal_source(effect_source):
            return ContingentCommencementClaimValidation(
                validated=False,
                rule_id=CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID,
                reason=(
                    "bound effect source does not carry a conditional-temporal-repeal "
                    "shape; claim is not anchored to a real conditional repeal"
                ),
                detail={"effect_source_preview": effect_source[:240]},
                **base,
            )

    # 3. Witness.
    witness_error = _witness_error(claim)
    if witness_error:
        return ContingentCommencementClaimValidation(
            validated=False,
            rule_id=CLAIM_REJECTED_WITNESS_RULE_ID,
            reason=witness_error,
            detail={
                "resolution": claim.resolution,
                "witness_si_id": claim.witness_si_id,
                "commenced_by_date": claim.commenced_by_date,
                "deadline_date": claim.deadline_date,
            },
            **base,
        )

    return ContingentCommencementClaimValidation(
        validated=True,
        rule_id=CLAIM_VALIDATED_RULE_ID,
        reason=(
            "owned conditional-temporal-repeal resolution is well-formed, bound to "
            "a real conditional-repeal source, and witnessed"
        ),
        detail={
            "resolution": claim.resolution,
            "repeal_fires_on": claim.repeal_fires_on,
            "deadline_date": claim.deadline_date,
            "trigger_id": claim.trigger_id,
        },
        **base,
    )


def _schema_error(claim: ContingentCommencementClaim) -> str:
    if claim.claim_kind not in _CLAIM_KINDS:
        return f"unsupported claim_kind {claim.claim_kind!r}"
    if not claim.claim_id:
        return "missing claim_id"
    if not claim.statute_id:
        return "missing statute_id"
    if not claim.effect_id:
        return "missing effect_id"
    if not claim.trigger_id:
        return "missing trigger_id"
    if not _is_iso_date(claim.deadline_date):
        return f"deadline_date {claim.deadline_date!r} is not an ISO date"
    if not claim.source_snippet:
        return "missing source_snippet"
    if claim.resolution not in _RESOLUTION_KINDS:
        return f"unsupported resolution {claim.resolution!r}"
    if claim.repeal_fires_on not in _REPEAL_FIRES_ON_VALUES:
        return f"unsupported repeal_fires_on {claim.repeal_fires_on!r}"
    return ""


def _witness_error(claim: ContingentCommencementClaim) -> str:
    if claim.resolution == RESOLUTION_COMMENCED:
        if not claim.witness_si_id:
            return "commenced resolution requires a witness_si_id"
        if not _is_iso_date(claim.commenced_by_date):
            return (
                f"commenced resolution requires an ISO commenced_by_date, got "
                f"{claim.commenced_by_date!r}"
            )
        if claim.commenced_by_date > claim.deadline_date:
            return (
                "commenced_by_date is after the deadline_date; the trigger did not "
                "commence within the contingency window"
            )
        return ""
    # did_not_commence
    if claim.witness_si_id:
        return "did_not_commence resolution must not carry a commencement witness_si_id"
    if claim.commenced_by_date:
        return "did_not_commence resolution must not carry a commenced_by_date"
    return ""


@dataclass(frozen=True, slots=True)
class ContingentRepealGateResult:
    """Whether a validated claim makes its conditional repeal apply at a PIT."""

    claim_id: str
    effect_id: str
    applies: bool
    rule_id: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "effect_id": self.effect_id,
            "applies": self.applies,
            "rule_id": self.rule_id,
            "reason": self.reason,
            "owner_phase": UK_PHASE_EFFECT_METADATA_FRONTEND,
        }


CONTINGENT_REPEAL_APPLIED_RULE_ID = "uk_contingent_commencement_repeal_applied_at_pit"
CONTINGENT_REPEAL_WITHHELD_PRE_DEADLINE_RULE_ID = (
    "uk_contingent_commencement_repeal_withheld_pre_deadline"
)
CONTINGENT_REPEAL_WITHHELD_TRIGGER_RULE_ID = (
    "uk_contingent_commencement_repeal_withheld_trigger_not_fired"
)


def gate_contingent_repeal_at_pit(
    claim: ContingentCommencementClaim,
    pit_date: str,
) -> ContingentRepealGateResult:
    """Decide whether a VALIDATED claim's conditional repeal applies at ``pit_date``.

    Precondition: ``claim`` has passed ``validate_contingent_commencement_claim``.
    This is the deterministic gate consumed by replay:

    - the repeal can never apply before the deadline date (the contingency is
      measured at the deadline);
    - at/after the deadline it applies iff the owned resolution is the one the
      repeal fires on (default: it fires when the trigger did NOT commence).
    """
    if str(pit_date or "") < claim.deadline_date:
        return ContingentRepealGateResult(
            claim_id=claim.claim_id,
            effect_id=claim.effect_id,
            applies=False,
            rule_id=CONTINGENT_REPEAL_WITHHELD_PRE_DEADLINE_RULE_ID,
            reason=(
                f"pit_date {pit_date} is before the contingency deadline "
                f"{claim.deadline_date}; conditional repeal cannot apply yet"
            ),
        )
    if claim.resolution == claim.repeal_fires_on:
        return ContingentRepealGateResult(
            claim_id=claim.claim_id,
            effect_id=claim.effect_id,
            applies=True,
            rule_id=CONTINGENT_REPEAL_APPLIED_RULE_ID,
            reason=(
                f"at pit_date {pit_date} the owned resolution {claim.resolution!r} is "
                f"the trigger the repeal fires on; conditional repeal applies"
            ),
        )
    return ContingentRepealGateResult(
        claim_id=claim.claim_id,
        effect_id=claim.effect_id,
        applies=False,
        rule_id=CONTINGENT_REPEAL_WITHHELD_TRIGGER_RULE_ID,
        reason=(
            f"the owned resolution {claim.resolution!r} is not the trigger the repeal "
            f"fires on ({claim.repeal_fires_on!r}); conditional repeal withheld"
        ),
    )
