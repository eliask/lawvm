"""Owned manual-compilation claim for UK savings-scoped text omission.

The frontier family **savings-qualified text omission**: a repeal/omission that
is qualified by a SAVINGS clause, e.g.::

    "In section 5(2) omit the words 'and the registrar', except in the case of a
     person who immediately before the commencement of this section held office
     as registrar."

The source omits text *only subject to* an exception/savings class — the text is
removed but a saving preserves its effect for a scope, window, or category.
Replay must NOT compile this as an unconditional text omission: doing so would
silently OVER-omit text the saving preserves. Determining the post-omission
consolidated text requires an owned decision about the saving's *scope* (which
occurrences/applications survive); it cannot be lowered deterministically.

Today LawVM has a *sensor* for this shape: ``source_adjudication`` classifies the
effect as ``savings_qualified_text_omission_unsupported`` (manual frontier
``uk_manual_frontier_savings_qualified_text_omission_candidate``), keeping the
omission off replay. This module is the missing *claim half*: an owned, typed,
deterministically-validated record of the saving's scope.

Contract (mirrors ``deixis_application_claim`` / the house style):

- A claim PROPOSES legal meaning (the saving's scope). It does NOT mutate base
  text. The safe default for this family is **under-application** (AGENTS.md
  §2.1): a validated claim emits a NON-replayable typed finding recording the
  preserved saving scope, never a text op — and NEVER a silent over-omission. The
  base text is left intact; the finding is the input a future applicability-aware
  compiler would consume to render the scoped post-omission reading.
- A deterministic validator binds the claim to a REAL savings-qualified omission
  effect (reusing the existing ``source_adjudication`` classifier), checks the
  bound effect id, and verifies the saving's scope predicate is well-formed
  against the target. It NEVER infers a scope.
- With NO claim authored, replay is byte-unchanged: the effect stays on the
  manual frontier exactly as today, and no finding is emitted.

Scope boundary: this module owns ONLY the *finding* half — recording the saving's
scope so it is no longer an undifferentiated frontier row. The applicability
overlay (compiling the scoped post-omission text, preserving the saved
occurrences) is deferred; this module deliberately emits no overlay and mutates
no base text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from lawvm.uk_legislation.phase_discipline import UK_PHASE_EFFECT_METADATA_FRONTEND
from lawvm.uk_legislation.source_adjudication import (
    _looks_like_savings_qualified_text_omission,
)

# ── Claim kind + rule ids ────────────────────────────────────────────────────
SAVINGS_SCOPED_OMISSION_CLAIM_KIND = "savings_scoped_omission"
_CLAIM_KINDS = frozenset({SAVINGS_SCOPED_OMISSION_CLAIM_KIND})

# Manual-frontier rule id this claim family advertises. Already registered in
# ``UK_MANUAL_CLAIM_TEMPLATE_RULE_IDS`` (the ``savings_qualified_text_omission``
# family), and consistent with the existing
# ``savings_qualified_text_omission_unsupported`` source-adjudication shape.
SAVINGS_SCOPED_OMISSION_CLAIM_TEMPLATE_RULE_ID = (
    "uk_manual_frontier_savings_qualified_text_omission_candidate"
)

# Proof-semantic id for the claim's owned determination. Already registered in
# ``UK_OPERATION_FAMILY_PROOF_SEMANTICS`` (the savings-qualified omission family).
SAVINGS_SCOPED_OMISSION_PROOF_SEMANTIC = (
    "savings_qualified_omission_applicability_scope"
)

# Validator rule ids (named per §0/§7: every owned determination is traceable).
CLAIM_VALIDATED_RULE_ID = "uk_savings_scoped_omission_claim_validated"
CLAIM_REJECTED_SCHEMA_RULE_ID = "uk_savings_scoped_omission_claim_rejected_schema"
CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID = (
    "uk_savings_scoped_omission_claim_rejected_source_mismatch"
)
CLAIM_REJECTED_SCOPE_RULE_ID = (
    "uk_savings_scoped_omission_claim_rejected_scope"
)

# Gate rule ids: the gate emits a NON-replayable finding (never a text op).
SAVINGS_SCOPED_OMISSION_FINDING_EMITTED_RULE_ID = (
    "uk_savings_scoped_omission_preserved_scope_finding"
)
SAVINGS_SCOPED_OMISSION_FINDING_WITHHELD_RULE_ID = (
    "uk_savings_scoped_omission_finding_withheld_unvalidated"
)

# Recognized saving bases. The validator only accepts these named kinds; it never
# infers the basis from the effect shape.
#   - ``category``: the saving preserves the omitted text for a named CATEGORY of
#     person/case (e.g. "except in the case of a person who ... held office").
#   - ``temporal_window``: the saving preserves the omitted text for matters
#     arising in a time WINDOW (e.g. "except ... begun before commencement").
#   - ``cross_reference``: the saving is defined by reference to another provision
#     or instrument (e.g. "except as provided by paragraph 3 of Schedule 2").
BASIS_CATEGORY = "category"
BASIS_TEMPORAL_WINDOW = "temporal_window"
BASIS_CROSS_REFERENCE = "cross_reference"
_RECOGNIZED_BASES = frozenset(
    {BASIS_CATEGORY, BASIS_TEMPORAL_WINDOW, BASIS_CROSS_REFERENCE}
)


@dataclass(frozen=True, slots=True)
class SavingsScopedOmissionClaim:
    """Owned determination recording the scope of a savings-qualified omission.

    Fields:

    - ``claim_id``: stable id for the claim row.
    - ``claim_kind``: ``savings_scoped_omission``.
    - ``statute_id`` / ``effect_id``: the affected statute and the bound effect
      (the savings-qualified text omission).
    - ``affected_target``: the affected provision the omission acts on (e.g.
      ``ukpga/1999/22 s. 5(2)``).
    - ``omitted_text``: the bounded quote of the text the source omits (the
      omission preimage). The saving preserves THIS text for the declared scope.
    - ``omission_anchor``: the structural anchor the omission acts at (e.g. the
      carrier provision / quoted phrase locus), used to bound the finding.
    - ``saving_basis``: a recognized basis (category / temporal_window /
      cross_reference).
    - ``saving_scope``: the bounded scope predicate — the surviving category /
      window / cross-reference the saving preserves the omitted text for.
    - ``saving_snippet``: bounded quote of the savings clause source surface
      ("except in the case of ..."). The validator rejects the claim if the
      effect surface is not a real savings-qualified omission.
    - ``source_snippet``: bounded quote of the full omission-with-saving source
      the claim binds to. The validator rejects free-form overrides.
    - ``claimant`` / ``claim_status``: provenance and lifecycle.
    """

    claim_id: str
    claim_kind: str
    statute_id: str
    effect_id: str
    affected_target: str
    omitted_text: str
    omission_anchor: str
    saving_basis: str
    saving_scope: str
    saving_snippet: str
    source_snippet: str
    claimant: str = ""
    claim_status: str = "proposed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "claim_kind": self.claim_kind,
            "statute_id": self.statute_id,
            "effect_id": self.effect_id,
            "affected_target": self.affected_target,
            "omitted_text": self.omitted_text,
            "omission_anchor": self.omission_anchor,
            "saving_basis": self.saving_basis,
            "saving_scope": self.saving_scope,
            "saving_snippet": self.saving_snippet,
            "source_snippet": self.source_snippet,
            "claimant": self.claimant,
            "claim_status": self.claim_status,
        }


@dataclass(frozen=True, slots=True)
class SavingsScopedOmissionClaimValidation:
    """Deterministic validation result for a savings-scoped omission claim."""

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


def claim_from_dict(row: Any) -> SavingsScopedOmissionClaim:
    """Build a claim carrier from a mapping row.

    This is a plain deserializer; it does not validate — call
    ``validate_savings_scoped_omission_claim``.
    """
    get = row.get
    return SavingsScopedOmissionClaim(
        claim_id=str(get("claim_id") or ""),
        claim_kind=str(get("claim_kind") or ""),
        statute_id=str(get("statute_id") or ""),
        effect_id=str(get("effect_id") or ""),
        affected_target=str(get("affected_target") or ""),
        omitted_text=str(get("omitted_text") or ""),
        omission_anchor=str(get("omission_anchor") or ""),
        saving_basis=str(get("saving_basis") or ""),
        saving_scope=str(get("saving_scope") or ""),
        saving_snippet=str(get("saving_snippet") or ""),
        source_snippet=str(get("source_snippet") or ""),
        claimant=str(get("claimant") or ""),
        claim_status=str(get("claim_status") or "proposed"),
    )


def _effect_savings_omission_source_text(
    effect: Any, extracted_source_text: Optional[str] = None
) -> str:
    """Best-effort source-text surface for an effect's savings-omission binding.

    The ``omit ... except in the case of ...`` shape can live in the effect type
    (the feed's verb phrase) or an attached source snippet. We concatenate the
    available surfaces so the binding check is robust to which surface carries the
    phrasing; the classifier itself is the arbiter of whether the shape is real.

    On REAL feed effects the effect attributes are empty — the savings-qualified
    omission prose lives in the extracted affecting XML, which the replay pipeline
    passes in as ``extracted_source_text`` (the same surface the manual-frontier
    classifier binds). When supplied it is concatenated first; the effect
    attributes remain as a fallback so synthetic unit-fixture effects keep binding.
    """
    parts: list[str] = []
    if extracted_source_text:
        parts.append(str(extracted_source_text))
    for attr in ("source_text", "raw_text", "effect_type", "comments"):
        value = getattr(effect, attr, "") or ""
        if value:
            parts.append(str(value))
    return " ".join(parts)


def _scope_consistency_error(claim: SavingsScopedOmissionClaim) -> str:
    """Return "" when the saving's scope predicate is well-formed against target.

    Stage 3. The check is deterministic and NEVER infers a scope; it only
    confirms the OWNED scope predicate is well-formed and coherent with the
    declared omission:

    1. the bounded saving scope must actually appear in the savings-clause
       snippet (the owned scope is anchored to the source saving, not invented);
    2. the omitted text must NOT itself contain the saving snippet — a saving that
       quotes back the entire omitted span is not a scope predicate;
    3. for the ``cross_reference`` basis, the scope must name a reference target
       (a provision/instrument token), distinguishing it from a free-text
       category. This guards against silent over-omission: a saving whose scope is
       not coherently bounded is rejected rather than compiled.
    """
    scope = (claim.saving_scope or "").strip()
    if not scope:
        return "missing saving_scope"
    saving = (claim.saving_snippet or "")
    if scope.lower() not in saving.lower():
        return (
            "saving_scope is not contained in the saving_snippet; the owned scope "
            "is not anchored to the source saving clause"
        )
    if claim.omitted_text and saving and saving.lower() in claim.omitted_text.lower():
        return (
            "saving_snippet is contained in the omitted_text; the saving does not "
            "bound a surviving scope distinct from the omitted span"
        )
    if claim.saving_basis == BASIS_CROSS_REFERENCE:
        lowered = scope.lower()
        if not any(
            token in lowered
            for token in (
                "section",
                "paragraph",
                "schedule",
                "regulation",
                "article",
                "subsection",
                "sub-paragraph",
            )
        ):
            return (
                "cross_reference basis requires the saving_scope to name a "
                "provision/instrument reference target"
            )
    return ""


def validate_savings_scoped_omission_claim(
    claim: SavingsScopedOmissionClaim,
    *,
    effect: Optional[Any] = None,
    extracted_source_text: Optional[str] = None,
) -> SavingsScopedOmissionClaimValidation:
    """Deterministically validate one savings-scoped omission claim.

    Stages, in order:

    1. **Schema** — claim kind, ids, the affected target, the omitted text /
       anchor, a recognized saving basis, the saving scope, and the saving / full
       source snippets are well-formed.
    2. **Source binding** — the claim's ``source_snippet`` matches a REAL
       savings-qualified omission effect (reusing the existing
       ``source_adjudication`` classifier ``_looks_like_savings_qualified_text_omission``),
       rejecting free-form overrides and plain (unconditional) omissions. When an
       ``effect`` is supplied, its ids must match the claim and its source surface
       must ALSO carry the savings-qualified omission shape.
    3. **Scope consistency** — the saving's scope predicate is well-formed against
       the target (anchored to the saving snippet, distinct from the omitted span,
       and — for the cross-reference basis — naming a reference target).

    The validator NEVER infers a scope; it only accepts an owned one. The safe
    default remains under-application: a rejected claim leaves the omission on the
    manual frontier rather than risking a silent over-omission.
    """
    base = {
        "claim_id": claim.claim_id,
        "statute_id": claim.statute_id,
        "effect_id": claim.effect_id,
        "proof_semantic": SAVINGS_SCOPED_OMISSION_PROOF_SEMANTIC,
    }

    # 1. Schema.
    schema_error = _schema_error(claim)
    if schema_error:
        return SavingsScopedOmissionClaimValidation(
            validated=False,
            rule_id=CLAIM_REJECTED_SCHEMA_RULE_ID,
            reason=schema_error,
            detail={
                "claim_kind": claim.claim_kind,
                "saving_basis": claim.saving_basis,
            },
            **base,
        )

    # 2. Source binding.
    if not _looks_like_savings_qualified_text_omission(claim.source_snippet):
        return SavingsScopedOmissionClaimValidation(
            validated=False,
            rule_id=CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID,
            reason=(
                "claim source_snippet is not a real savings-qualified text omission "
                "effect; the claim may not scope an omission for a free-form or "
                "unconditional (non-savings) effect"
            ),
            detail={"source_snippet": claim.source_snippet[:240]},
            **base,
        )
    if effect is not None:
        effect_id = str(getattr(effect, "effect_id", "") or "")
        if effect_id and claim.effect_id and effect_id != claim.effect_id:
            return SavingsScopedOmissionClaimValidation(
                validated=False,
                rule_id=CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID,
                reason=(
                    "claim effect_id does not match the bound effect; claim must "
                    "bind to the real savings-qualified omission effect"
                ),
                detail={"bound_effect_id": effect_id},
                **base,
            )
        effect_source = _effect_savings_omission_source_text(
            effect, extracted_source_text
        )
        if not _looks_like_savings_qualified_text_omission(effect_source):
            return SavingsScopedOmissionClaimValidation(
                validated=False,
                rule_id=CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID,
                reason=(
                    "bound effect source does not carry the savings-qualified "
                    "omission shape; claim is not anchored to a real savings omission"
                ),
                detail={"effect_source_preview": effect_source[:240]},
                **base,
            )

    # 3. Scope consistency.
    scope_error = _scope_consistency_error(claim)
    if scope_error:
        return SavingsScopedOmissionClaimValidation(
            validated=False,
            rule_id=CLAIM_REJECTED_SCOPE_RULE_ID,
            reason=scope_error,
            detail={
                "saving_basis": claim.saving_basis,
                "saving_scope": claim.saving_scope[:240],
                "affected_target": claim.affected_target,
            },
            **base,
        )

    return SavingsScopedOmissionClaimValidation(
        validated=True,
        rule_id=CLAIM_VALIDATED_RULE_ID,
        reason=(
            "owned savings-scoped omission is well-formed, bound to a real savings-"
            "qualified omission effect, and carries a scope predicate that is "
            "coherent against the target"
        ),
        detail={
            "affected_target": claim.affected_target,
            "saving_basis": claim.saving_basis,
            "saving_scope": claim.saving_scope[:240],
        },
        **base,
    )


def _schema_error(claim: SavingsScopedOmissionClaim) -> str:
    if claim.claim_kind not in _CLAIM_KINDS:
        return f"unsupported claim_kind {claim.claim_kind!r}"
    if not claim.claim_id:
        return "missing claim_id"
    if not claim.statute_id:
        return "missing statute_id"
    if not claim.effect_id:
        return "missing effect_id"
    if not claim.affected_target.strip():
        return "missing affected_target"
    if not claim.omitted_text.strip():
        return "missing omitted_text"
    if not claim.omission_anchor.strip():
        return "missing omission_anchor"
    if claim.saving_basis not in _RECOGNIZED_BASES:
        return f"unsupported saving_basis {claim.saving_basis!r}"
    if not claim.saving_scope.strip():
        return "missing saving_scope"
    if not claim.saving_snippet.strip():
        return "missing saving_snippet"
    if not claim.source_snippet:
        return "missing source_snippet"
    return ""


@dataclass(frozen=True, slots=True)
class SavingsScopedOmissionFinding:
    """A NON-replayable typed finding recording a preserved saving scope.

    This is the deliverable: a record (NOT a text op) that names the affected
    target, the omitted text, and the saving's scope (category / window /
    cross-reference) that preserves it. It leaves the base text intact (the safe
    default, §2.1 — never a silent over-omission) and is the input a future
    applicability-aware compiler would consume to render the scoped post-omission
    reading.
    """

    claim_id: str
    effect_id: str
    statute_id: str
    affected_target: str
    omitted_text: str
    omission_anchor: str
    saving_basis: str
    saving_scope: str
    rule_id: str
    replayable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "effect_id": self.effect_id,
            "statute_id": self.statute_id,
            "affected_target": self.affected_target,
            "omitted_text": self.omitted_text,
            "omission_anchor": self.omission_anchor,
            "saving_basis": self.saving_basis,
            "saving_scope": self.saving_scope,
            "rule_id": self.rule_id,
            "replayable": self.replayable,
            "proof_semantic": SAVINGS_SCOPED_OMISSION_PROOF_SEMANTIC,
            "owner_phase": UK_PHASE_EFFECT_METADATA_FRONTEND,
        }


@dataclass(frozen=True, slots=True)
class SavingsScopedOmissionGateResult:
    """Whether a validated claim emits its preserved-scope finding."""

    claim_id: str
    effect_id: str
    emitted: bool
    rule_id: str
    reason: str
    finding: Optional[SavingsScopedOmissionFinding] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "effect_id": self.effect_id,
            "emitted": self.emitted,
            "rule_id": self.rule_id,
            "reason": self.reason,
            "owner_phase": UK_PHASE_EFFECT_METADATA_FRONTEND,
        }


def gate_savings_scoped_omission_claim(
    claim: SavingsScopedOmissionClaim,
    *,
    validated: bool = False,
) -> SavingsScopedOmissionGateResult:
    """Emit the preserved-scope finding for a VALIDATED claim, else withhold.

    Precondition: ``validated`` reflects the result of
    ``validate_savings_scoped_omission_claim`` for this claim. Only a VALIDATED
    claim produces a ``SavingsScopedOmissionFinding`` — a NON-replayable typed
    record of the saving's preserved scope. An unvalidated/mismatched claim
    withholds (returns no finding), so absent or invalid claim ⇒ no finding and
    the base text is byte-unchanged. This gate NEVER emits a text op: it owns the
    finding half only; it never risks the silent over-omission an unconditional
    text op would cause.
    """
    if not validated:
        return SavingsScopedOmissionGateResult(
            claim_id=claim.claim_id,
            effect_id=claim.effect_id,
            emitted=False,
            rule_id=SAVINGS_SCOPED_OMISSION_FINDING_WITHHELD_RULE_ID,
            reason=(
                "savings-scoped omission claim is not validated; the finding is "
                "withheld and the effect stays on the manual frontier"
            ),
        )
    finding = SavingsScopedOmissionFinding(
        claim_id=claim.claim_id,
        effect_id=claim.effect_id,
        statute_id=claim.statute_id,
        affected_target=claim.affected_target,
        omitted_text=claim.omitted_text,
        omission_anchor=claim.omission_anchor,
        saving_basis=claim.saving_basis,
        saving_scope=claim.saving_scope,
        rule_id=SAVINGS_SCOPED_OMISSION_FINDING_EMITTED_RULE_ID,
    )
    return SavingsScopedOmissionGateResult(
        claim_id=claim.claim_id,
        effect_id=claim.effect_id,
        emitted=True,
        rule_id=SAVINGS_SCOPED_OMISSION_FINDING_EMITTED_RULE_ID,
        reason=(
            "validated savings-scoped omission claim records the saving's preserved "
            "scope; emitting a non-replayable typed finding (no base-text mutation, "
            "no over-omission)"
        ),
        finding=finding,
    )
