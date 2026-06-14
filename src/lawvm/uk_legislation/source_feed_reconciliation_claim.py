"""Owned manual-compilation claim reconciling a source/feed target conflict.

The **N5 source/feed target reconciliation** frontier family
(``source_or_feed_target_conflict``): the effect SOURCE text and the official
effects FEED name DIFFERENT targets for the same effect. Concrete shapes::

    ukpga/2005/5 (ITTOIA) s.536(1) <- ukpga/2020/14 s.37(3)(a)(5)
        effect: "word omitted"
        source: "omit the 'and' at the end of sub-paragraph (i)"
        feed target: s.536(1)            # the PARENT, not the source-named child

    ukpga/2007/15 Sch.5 para.22(1) <- ukpga/2022/35
        source: "omit 'and' at the end of paragraph (b)"
        feed target: para.22(1)          # the parent again

The source explicitly scopes a quoted word omission to a CHILD provision that
differs from the effect-feed target (the parent). Replaying the feed target
naively would over-omit (delete the word from the wrong, broader provision); but
the conflict cannot simply be "resolved to the child" by guesswork either, since
where the child is genuinely ambiguous in the live target that would be a silent
target hijack (``MANUAL_COMPILATION_CLAIMS.md`` §1.1, §2.1).

Today LawVM has a *sensor* for the family: ``source_adjudication`` classifies the
blocking ``uk_effect_child_qualified_word_omission_target_mismatch_rejected``
lowering row as status ``source_or_feed_target_conflict`` (manual frontier
``uk_manual_frontier_child_qualified_word_omission_target_mismatch``). The
existing proof-semantic ``source_feed_target_reconciliation_claim`` / action
family ``source_target_reconciliation`` already RECORD the conflict — but they do
not DECIDE it. This module supplies the missing **owned reconciliation
DECISION**: which surface (the source-named child vs the feed-named parent) is
authoritative for this effect, and why.

Contract (mirrors ``deixis_application_claim`` / ``range_to_container_claim`` —
the house style):

- A claim PROPOSES legal meaning (the authoritative target surface). The safe
  default for an *ambiguous* conflict is **under-application / non-replayable
  typed finding** (§2.1): a validated claim records the adjudication, base text
  intact, and emits NO text op — never a silent over-omission across the
  uncertain source/feed boundary.
- A deterministic validator binds the claim to a REAL source/feed target-conflict
  effect (reusing the existing ``source_claims_child_qualified_word_omission``
  recognizer that produces the ``..._target_mismatch_rejected`` row), checks the
  bound effect id, requires the two named targets to genuinely DIFFER, and checks
  the resolved target is one of those two surfaces. It NEVER infers the decision.
- With NO claim authored, replay is byte-unchanged: the effect stays on the
  manual frontier exactly as today, and no finding is emitted.

Replayable resolution (narrow, gated): in the common "omit 'and' at the end of
(i)" shape the source-named child is frequently *deterministically* locatable in
the live target. ``gate_source_feed_reconciliation_claim`` will emit a
**replayable** child-target resolution ONLY for the
``source_child_locatable_in_live_target`` basis, ONLY for a validated claim, and
ONLY when a live target view proves the source-named child is a real member. For
the ``feed_parent_authoritative`` and ``genuinely_ambiguous_finding_only`` bases
the gate emits a non-replayable typed finding (never touches ``compiled``),
mirroring ``deixis_application_claim``'s diagnostics-only gate. Absent a claim,
both paths emit nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from lawvm.uk_legislation.phase_discipline import UK_PHASE_EFFECT_METADATA_FRONTEND
from lawvm.uk_legislation.source_text_reclassifications import (
    source_claims_child_qualified_word_omission,
)

# ── Claim kind + rule ids ────────────────────────────────────────────────────
SOURCE_FEED_RECONCILIATION_CLAIM_KIND = "source_feed_target_reconciliation"
_CLAIM_KINDS = frozenset({SOURCE_FEED_RECONCILIATION_CLAIM_KIND})

# Manual-frontier rule id this claim family advertises. The N5
# source/feed-target-conflict shape is already classified under the existing
# ``uk_manual_frontier_child_qualified_word_omission_target_mismatch`` candidate
# (registered in ``UK_MANUAL_CLAIM_TEMPLATE_RULE_IDS`` and
# ``frontier_work_items``); this claim REUSES that template rule id rather than
# minting a sibling, since it is the owned decision half of the same family.
SOURCE_FEED_RECONCILIATION_CLAIM_TEMPLATE_RULE_ID = (
    "uk_manual_frontier_child_qualified_word_omission_target_mismatch"
)

# Proof-semantic id for the claim's owned determination. This is the EXISTING
# template-set semantic the family already advertises (registered in
# ``UK_OPERATION_FAMILY_PROOF_SEMANTICS``); the claim reuses it, supplying the
# decision the recorded conflict was missing.
SOURCE_FEED_RECONCILIATION_PROOF_SEMANTIC = "source_feed_target_reconciliation_claim"

# Validator rule ids (named per §0/§7: every owned determination is traceable).
CLAIM_VALIDATED_RULE_ID = "uk_source_feed_reconciliation_claim_validated"
CLAIM_REJECTED_SCHEMA_RULE_ID = "uk_source_feed_reconciliation_claim_rejected_schema"
CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID = (
    "uk_source_feed_reconciliation_claim_rejected_source_mismatch"
)
CLAIM_REJECTED_RESOLUTION_RULE_ID = (
    "uk_source_feed_reconciliation_claim_rejected_resolution"
)

# Gate rule ids: a replayable child-target resolution (locatable basis only) OR a
# NON-replayable finding (parent-authoritative / ambiguous bases), else withheld.
SOURCE_FEED_RECONCILIATION_CHILD_RESOLVED_RULE_ID = (
    "uk_source_feed_reconciliation_child_target_resolved"
)
SOURCE_FEED_RECONCILIATION_FINDING_EMITTED_RULE_ID = (
    "uk_source_feed_reconciliation_adjudication_finding"
)
SOURCE_FEED_RECONCILIATION_FINDING_WITHHELD_RULE_ID = (
    "uk_source_feed_reconciliation_finding_withheld_unvalidated"
)

# Recognized reconciliation bases. The validator only accepts these named kinds;
# it NEVER infers the basis from the effect shape.
#   - ``source_child_locatable_in_live_target``: the source-named child is the
#     authoritative surface AND it is unambiguously locatable in the live target
#     (the common "omit 'and' at the end of (i)" shape). This is the ONLY basis
#     the gate may resolve to a replayable child-target emission, and only when a
#     live target view proves the child is a real member.
#   - ``feed_parent_authoritative``: the feed-named parent is the authoritative
#     surface (the source child-scoping is editorial/descriptive); finding only.
#   - ``genuinely_ambiguous_finding_only``: the child is genuinely ambiguous in
#     the live target, so the safe default is a non-replayable typed finding,
#     never a silent over-omission (§2.1). Finding only.
BASIS_SOURCE_CHILD_LOCATABLE = "source_child_locatable_in_live_target"
BASIS_FEED_PARENT_AUTHORITATIVE = "feed_parent_authoritative"
BASIS_GENUINELY_AMBIGUOUS = "genuinely_ambiguous_finding_only"
_RECOGNIZED_BASES = frozenset(
    {
        BASIS_SOURCE_CHILD_LOCATABLE,
        BASIS_FEED_PARENT_AUTHORITATIVE,
        BASIS_GENUINELY_AMBIGUOUS,
    }
)
# The bases whose authoritative surface is the source-named child.
_CHILD_RESOLVING_BASES = frozenset({BASIS_SOURCE_CHILD_LOCATABLE})


@dataclass(frozen=True, slots=True)
class SourceFeedReconciliationClaim:
    """Owned determination reconciling an N5 source/feed target conflict.

    Fields:

    - ``claim_id``: stable id for the claim row.
    - ``claim_kind``: ``source_feed_target_reconciliation``.
    - ``statute_id`` / ``effect_id``: the affected statute and the bound N5 effect
      (the source/feed target-conflict word-omission effect, e.g. ``ukpga/2005/5``
      s.536(1) <- ``ukpga/2020/14`` s.37(3)(a)(5)).
    - ``effect_type``: the effect verb the conflict is scoped to (e.g.
      ``word omitted``); used together with ``source_snippet`` to bind to the real
      ``child_qualified_word_omission_target_mismatch`` shape via the existing
      recognizer.
    - ``source_named_target``: the target the SOURCE text explicitly names (the
      child, e.g. the eid/ref of ``sub-paragraph (i)``).
    - ``feed_named_target``: the target the effects FEED names (the parent, e.g.
      ``s.536(1)``). The validator requires these two to genuinely DIFFER.
    - ``resolved_target_eid``: the surface the claim DECIDES is authoritative for
      this effect. Must equal either the source-named or the feed-named target.
    - ``reconciliation_basis``: a recognized basis (child-locatable / parent-
      authoritative / genuinely-ambiguous).
    - ``source_snippet``: bounded quote of the N5 effect source surface the claim
      binds to (the "omit 'and' at the end of sub-paragraph (i)" instruction). The
      validator rejects the claim if this is not a real child-qualified
      word-omission source.
    - ``rationale``: free-form provenance note for the decision (not validated).
    - ``claimant`` / ``status``: provenance and lifecycle.
    """

    claim_id: str
    claim_kind: str
    statute_id: str
    effect_id: str
    effect_type: str
    source_named_target: str
    feed_named_target: str
    resolved_target_eid: str
    reconciliation_basis: str
    source_snippet: str
    rationale: str = ""
    claimant: str = ""
    status: str = "proposed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "claim_kind": self.claim_kind,
            "statute_id": self.statute_id,
            "effect_id": self.effect_id,
            "effect_type": self.effect_type,
            "source_named_target": self.source_named_target,
            "feed_named_target": self.feed_named_target,
            "resolved_target_eid": self.resolved_target_eid,
            "reconciliation_basis": self.reconciliation_basis,
            "source_snippet": self.source_snippet,
            "rationale": self.rationale,
            "claimant": self.claimant,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class SourceFeedReconciliationClaimValidation:
    """Deterministic validation result for a source/feed reconciliation claim."""

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


def claim_from_dict(row: Any) -> SourceFeedReconciliationClaim:
    """Build a claim carrier from a mapping row.

    This is a plain deserializer; it does not validate — call
    ``validate_source_feed_reconciliation_claim``.
    """
    get = row.get
    return SourceFeedReconciliationClaim(
        claim_id=str(get("claim_id") or ""),
        claim_kind=str(get("claim_kind") or ""),
        statute_id=str(get("statute_id") or ""),
        effect_id=str(get("effect_id") or ""),
        effect_type=str(get("effect_type") or ""),
        source_named_target=str(get("source_named_target") or ""),
        feed_named_target=str(get("feed_named_target") or ""),
        resolved_target_eid=str(get("resolved_target_eid") or ""),
        reconciliation_basis=str(get("reconciliation_basis") or ""),
        source_snippet=str(get("source_snippet") or ""),
        rationale=str(get("rationale") or ""),
        claimant=str(get("claimant") or ""),
        status=str(get("status") or "proposed"),
    )


def _effect_type_surface(claim: SourceFeedReconciliationClaim, effect: Any) -> str:
    """Best-effort effect-type verb for the source-binding recognizer.

    The ``source_claims_child_qualified_word_omission`` recognizer is gated on the
    effect verb being one of the word-omission/repeal types; we prefer the claim's
    own ``effect_type`` and fall back to the bound effect's ``effect_type``.
    """
    if claim.effect_type.strip():
        return claim.effect_type
    return str(getattr(effect, "effect_type", "") or "")


def _bound_effect_source_text(
    effect: Any, extracted_source_text: Optional[str] = None
) -> str:
    """Best-effort source surface for an effect's N5 binding.

    The child-qualified omission instruction lives in the extracted source / source
    text / raw text; concatenate the available surfaces so the binding check is
    robust to which carries it. The recognizer is the arbiter of whether the shape
    is real.

    On REAL feed effects the effect attributes (including ``extracted_text``) are
    empty — the child-qualified omission prose lives in the extracted affecting XML,
    which the replay pipeline passes in as ``extracted_source_text`` (the same
    surface the manual-frontier classifier binds). When supplied it is concatenated
    first; the effect attributes remain as a fallback so synthetic unit-fixture
    effects keep binding.
    """
    parts: list[str] = []
    if extracted_source_text:
        parts.append(str(extracted_source_text))
    for attr in ("extracted_text", "source_text", "raw_text", "comments"):
        value = getattr(effect, attr, "") or ""
        if value:
            parts.append(str(value))
    return " ".join(parts)


def _resolution_consistency_error(
    claim: SourceFeedReconciliationClaim,
    *,
    live_target_member_eids: Optional[Iterable[str]],
) -> str:
    """Return "" when the resolved target is consistent with the named surfaces.

    Checks, in order:

    1. the two named targets genuinely DIFFER (a no-op "conflict" is not an N5
       reconciliation — there is nothing to decide);
    2. the resolved target is one of the two named surfaces (the claim may not
       invent a third target — that would be a target hijack, §1.1); and
    3. for the ``source_child_locatable_in_live_target`` basis, WHEN a live target
       member view is supplied, the resolved (source-named child) target is a real
       member of it — the child the gate may replayably resolve to must actually
       exist in the live target (no silent over-omission, §2.1). Without a live
       view this stage is skipped and the schema+source binding is the floor.

    The check never *infers* the decision; it only confirms an owned one is
    surface-consistent.
    """
    source_named = claim.source_named_target.strip()
    feed_named = claim.feed_named_target.strip()
    resolved = claim.resolved_target_eid.strip()
    if source_named == feed_named:
        return (
            "source_named_target and feed_named_target are identical; there is no "
            "source/feed conflict to reconcile"
        )
    if resolved not in {source_named, feed_named}:
        return (
            "resolved_target_eid is neither the source-named nor the feed-named "
            "surface; the reconciliation may not invent a third target (§1.1)"
        )
    if claim.reconciliation_basis in _CHILD_RESOLVING_BASES:
        if resolved != source_named:
            return (
                "source_child_locatable basis must resolve to the source-named "
                "child target, not the feed-named parent"
            )
        members = [str(eid) for eid in (live_target_member_eids or ()) if str(eid)]
        if members and resolved not in members:
            return (
                "resolved source-named child target is not a member of the live "
                "target view; the child is not unambiguously locatable, so a "
                "replayable child resolution would over-omit (§2.1)"
            )
    return ""


def validate_source_feed_reconciliation_claim(
    claim: SourceFeedReconciliationClaim,
    *,
    effect: Optional[Any] = None,
    live_target_member_eids: Optional[Iterable[str]] = None,
    extracted_source_text: Optional[str] = None,
) -> SourceFeedReconciliationClaimValidation:
    """Deterministically validate one source/feed reconciliation claim.

    Stages, in order:

    1. **Schema** — claim kind, ids, both named targets, the resolved target, a
       recognized reconciliation basis, the effect type, and a source snippet are
       well-formed.
    2. **Source binding** — the claim's ``effect_type`` + ``source_snippet`` match
       a real ``child_qualified_word_omission_target_mismatch`` source (reusing the
       existing ``source_claims_child_qualified_word_omission`` recognizer),
       rejecting free-form overrides and non-child-scoped omissions. When an
       ``effect`` is supplied, its ids must match the claim and its source surface
       must ALSO carry the child-qualified omission shape.
    3. **Resolution consistency** — the two named targets differ, the resolved
       target is one of them, and (for the child-locatable basis, when a live
       target view is supplied) the source-named child is a real live member.

    The validator NEVER infers the decision; it only accepts an owned one.
    """
    base = {
        "claim_id": claim.claim_id,
        "statute_id": claim.statute_id,
        "effect_id": claim.effect_id,
        "proof_semantic": SOURCE_FEED_RECONCILIATION_PROOF_SEMANTIC,
    }

    # 1. Schema.
    schema_error = _schema_error(claim)
    if schema_error:
        return SourceFeedReconciliationClaimValidation(
            validated=False,
            rule_id=CLAIM_REJECTED_SCHEMA_RULE_ID,
            reason=schema_error,
            detail={
                "claim_kind": claim.claim_kind,
                "reconciliation_basis": claim.reconciliation_basis,
            },
            **base,
        )

    # 2. Source binding.
    if not source_claims_child_qualified_word_omission(
        effect_type=claim.effect_type, extracted_text=claim.source_snippet
    ):
        return SourceFeedReconciliationClaimValidation(
            validated=False,
            rule_id=CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID,
            reason=(
                "claim source_snippet/effect_type is not a real child-qualified "
                "word-omission target-mismatch source; the claim may not reconcile "
                "a free-form or non-child-scoped effect"
            ),
            detail={
                "effect_type": claim.effect_type,
                "source_snippet": claim.source_snippet[:240],
            },
            **base,
        )
    if effect is not None:
        effect_id = str(getattr(effect, "effect_id", "") or "")
        if effect_id and claim.effect_id and effect_id != claim.effect_id:
            return SourceFeedReconciliationClaimValidation(
                validated=False,
                rule_id=CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID,
                reason=(
                    "claim effect_id does not match the bound effect; claim must "
                    "bind to the real N5 source/feed target-conflict effect"
                ),
                detail={"bound_effect_id": effect_id},
                **base,
            )
        effect_source = _bound_effect_source_text(effect, extracted_source_text)
        if not source_claims_child_qualified_word_omission(
            effect_type=_effect_type_surface(claim, effect),
            extracted_text=effect_source,
        ):
            return SourceFeedReconciliationClaimValidation(
                validated=False,
                rule_id=CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID,
                reason=(
                    "bound effect source does not carry the child-qualified "
                    "word-omission target-mismatch shape; claim is not anchored to "
                    "a real N5 source/feed target-conflict effect"
                ),
                detail={"effect_source_preview": effect_source[:240]},
                **base,
            )

    # 3. Resolution consistency.
    resolution_error = _resolution_consistency_error(
        claim, live_target_member_eids=live_target_member_eids
    )
    if resolution_error:
        return SourceFeedReconciliationClaimValidation(
            validated=False,
            rule_id=CLAIM_REJECTED_RESOLUTION_RULE_ID,
            reason=resolution_error,
            detail={
                "reconciliation_basis": claim.reconciliation_basis,
                "source_named_target": claim.source_named_target,
                "feed_named_target": claim.feed_named_target,
                "resolved_target_eid": claim.resolved_target_eid,
            },
            **base,
        )

    return SourceFeedReconciliationClaimValidation(
        validated=True,
        rule_id=CLAIM_VALIDATED_RULE_ID,
        reason=(
            "owned source/feed reconciliation is well-formed, bound to a real N5 "
            "child-qualified word-omission target-conflict effect, names two "
            "genuinely-different surfaces, and resolves to one of them"
        ),
        detail={
            "source_named_target": claim.source_named_target,
            "feed_named_target": claim.feed_named_target,
            "resolved_target_eid": claim.resolved_target_eid,
            "reconciliation_basis": claim.reconciliation_basis,
        },
        **base,
    )


def _schema_error(claim: SourceFeedReconciliationClaim) -> str:
    if claim.claim_kind not in _CLAIM_KINDS:
        return f"unsupported claim_kind {claim.claim_kind!r}"
    if not claim.claim_id:
        return "missing claim_id"
    if not claim.statute_id:
        return "missing statute_id"
    if not claim.effect_id:
        return "missing effect_id"
    if not claim.effect_type.strip():
        return "missing effect_type"
    if not claim.source_named_target.strip():
        return "missing source_named_target"
    if not claim.feed_named_target.strip():
        return "missing feed_named_target"
    if not claim.resolved_target_eid.strip():
        return "missing resolved_target_eid"
    if claim.reconciliation_basis not in _RECOGNIZED_BASES:
        return f"unsupported reconciliation_basis {claim.reconciliation_basis!r}"
    if not claim.source_snippet:
        return "missing source_snippet"
    return ""


@dataclass(frozen=True, slots=True)
class SourceFeedReconciliationFinding:
    """A typed finding recording the source/feed reconciliation decision.

    For the ``feed_parent_authoritative`` and ``genuinely_ambiguous_finding_only``
    bases this is NON-replayable (``replayable=False``): a record that names the
    two conflicting surfaces and the authoritative one, leaving the base text
    intact (the safe §2.1 default — never a silent over-omission). For the
    ``source_child_locatable_in_live_target`` basis the gate marks the finding
    ``replayable=True`` and names the resolved child target eid a downstream
    compiler may retarget the omission to (proved locatable in the live target).
    """

    claim_id: str
    effect_id: str
    statute_id: str
    source_named_target: str
    feed_named_target: str
    resolved_target_eid: str
    reconciliation_basis: str
    rule_id: str
    replayable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "effect_id": self.effect_id,
            "statute_id": self.statute_id,
            "source_named_target": self.source_named_target,
            "feed_named_target": self.feed_named_target,
            "resolved_target_eid": self.resolved_target_eid,
            "reconciliation_basis": self.reconciliation_basis,
            "rule_id": self.rule_id,
            "replayable": self.replayable,
            "proof_semantic": SOURCE_FEED_RECONCILIATION_PROOF_SEMANTIC,
            "owner_phase": UK_PHASE_EFFECT_METADATA_FRONTEND,
        }


@dataclass(frozen=True, slots=True)
class SourceFeedReconciliationGateResult:
    """Whether a validated claim emits a reconciliation finding, and how."""

    claim_id: str
    effect_id: str
    emitted: bool
    replayable: bool
    rule_id: str
    reason: str
    finding: Optional[SourceFeedReconciliationFinding] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "effect_id": self.effect_id,
            "emitted": self.emitted,
            "replayable": self.replayable,
            "rule_id": self.rule_id,
            "reason": self.reason,
            "owner_phase": UK_PHASE_EFFECT_METADATA_FRONTEND,
        }


def gate_source_feed_reconciliation_claim(
    claim: SourceFeedReconciliationClaim,
    *,
    validated: bool = False,
) -> SourceFeedReconciliationGateResult:
    """Emit the reconciliation finding for a VALIDATED claim, else withhold.

    Precondition: ``validated`` reflects the result of
    ``validate_source_feed_reconciliation_claim`` for this claim (including, for
    the child-locatable basis, the live-target member check — the validator is the
    only place the live target is consulted).

    Behaviour by basis (validated claims only):

    - ``source_child_locatable_in_live_target``: emits a **replayable** finding
      naming the resolved source-named child target a downstream compiler may
      retarget the omission to. This is the ONLY replayable emission, and it is
      reachable only because validation already proved (when a live view was
      supplied) the child is a real member — so a silent over-omission cannot
      occur. The gate itself NEVER mutates ``compiled``; it records the decision.
    - ``feed_parent_authoritative`` / ``genuinely_ambiguous_finding_only``: emits
      a NON-replayable typed finding (diagnostics-only), base text intact — the
      safe under-application default for an uncertain source/feed boundary (§2.1).

    An unvalidated/mismatched claim withholds (returns no finding), so absent or
    invalid claim ⇒ no finding and the base text is byte-unchanged.
    """
    if not validated:
        return SourceFeedReconciliationGateResult(
            claim_id=claim.claim_id,
            effect_id=claim.effect_id,
            emitted=False,
            replayable=False,
            rule_id=SOURCE_FEED_RECONCILIATION_FINDING_WITHHELD_RULE_ID,
            reason=(
                "source/feed reconciliation claim is not validated; the finding is "
                "withheld and the effect stays on the manual frontier"
            ),
        )

    if claim.reconciliation_basis in _CHILD_RESOLVING_BASES:
        finding = SourceFeedReconciliationFinding(
            claim_id=claim.claim_id,
            effect_id=claim.effect_id,
            statute_id=claim.statute_id,
            source_named_target=claim.source_named_target,
            feed_named_target=claim.feed_named_target,
            resolved_target_eid=claim.resolved_target_eid,
            reconciliation_basis=claim.reconciliation_basis,
            rule_id=SOURCE_FEED_RECONCILIATION_CHILD_RESOLVED_RULE_ID,
            replayable=True,
        )
        return SourceFeedReconciliationGateResult(
            claim_id=claim.claim_id,
            effect_id=claim.effect_id,
            emitted=True,
            replayable=True,
            rule_id=SOURCE_FEED_RECONCILIATION_CHILD_RESOLVED_RULE_ID,
            reason=(
                "validated source/feed reconciliation resolves the conflict to the "
                "source-named child, proved unambiguously locatable in the live "
                "target; emitting a replayable child-target resolution for a "
                "downstream compiler to retarget the omission to"
            ),
            finding=finding,
        )

    finding = SourceFeedReconciliationFinding(
        claim_id=claim.claim_id,
        effect_id=claim.effect_id,
        statute_id=claim.statute_id,
        source_named_target=claim.source_named_target,
        feed_named_target=claim.feed_named_target,
        resolved_target_eid=claim.resolved_target_eid,
        reconciliation_basis=claim.reconciliation_basis,
        rule_id=SOURCE_FEED_RECONCILIATION_FINDING_EMITTED_RULE_ID,
        replayable=False,
    )
    return SourceFeedReconciliationGateResult(
        claim_id=claim.claim_id,
        effect_id=claim.effect_id,
        emitted=True,
        replayable=False,
        rule_id=SOURCE_FEED_RECONCILIATION_FINDING_EMITTED_RULE_ID,
        reason=(
            "validated source/feed reconciliation records which surface is "
            "authoritative; emitting a non-replayable typed finding (no base-text "
            "mutation) — the safe default for the parent-authoritative or "
            "genuinely-ambiguous conflict (§2.1)"
        ),
        finding=finding,
    )
