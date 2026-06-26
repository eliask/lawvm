"""Per-op apply-time closure sweeps (audit-registry wave 2: apply-authority closure).

This module hosts the per-op totality sweeps that run from inside the production
``_enforce_per_op_apply_authority`` boundary in :mod:`apply_resolved_op`, beside
the landed LS-01 / LS-03 / EV-05 per-op gates. Each sweep is the totality
assertion over one prime-directive fact that the wave-1 gates carry a *carrier*
for but did not yet *close*:

* **LS-05** — scope-confidence totality: every state-mutating op records HOW its
  scope was obtained (a typed :class:`ScopeConfidence`). An op that landed a write
  with an EMPTY scope-confidence reached apply with no scope-resolution witness.
  Non-blocking observation (corpus surfacing): a real op may legitimately carry no
  scope witness (a global unscoped target), so this records rather than blocks —
  tag-don't-guess. The blocking arm is the synthetic guard-liveness drill bite.
* **LS-06** — action-family conversion witness totality: every op whose emitted
  (resolved) action family differs from its originally-parsed action carries a
  named conversion witness (an extraction/target-guessing provenance tag). An
  unwitnessed verb conversion is swept against. Non-blocking observation.
* **LS-07** — granularity-escalation per-op: a descendant-granularity op
  (paragraph/item/special) that landed a write touching its host whole-unit is a
  granularity escalation. Strict-only blocking gate (mirrors LS-01/LS-03/EV-05):
  the bench/corpus replay runs permissive, so this stays 0-delta on bench by
  construction; strict mode BLOCKS the escalation.
* **LS-09** — payload-smuggling closure: every landed write's changed tree paths
  are admissible against the op's claimed target (a claim on a descendant child
  never authorizes its parent container). Reuses the per-op mutation-boundary
  verdict — a write that touched a STRICT ANCESTOR of the op's target is a
  parent-container smuggle. Non-blocking observation closure.
* **LS-10** — unstated-migration closure: a state-mutating op whose resolved
  target address differs from its originally-parsed (nominal) address — an
  address-key delta — is backed by a migration/lineage event OR a typed
  address-only-rekey witness; never a silent shortcut. Non-blocking observation.

All blocking arms fire ONLY under strict (``strict_profile is not None``), the
same firewall shape the wave-1 per-op gates use, so the permissive bench/corpus
replay stays byte-for-byte unchanged. The non-blocking observation arms record
regardless of profile (a permissive profile must not silently authorize an
unwitnessed conversion / smuggle / rekey) but never gate replay, so they are also
0-delta.
"""

from __future__ import annotations

from typing import Mapping, Optional

from lawvm.core.execution_authorization import ExecutionAuthorization
from lawvm.core.ir import LegalAddress
from lawvm.core.phase_result import Finding
from lawvm.finland.apply_policy import _OP_TYPE_TO_ACTION
from lawvm.finland.migration_ledger import MigrationLedger
from lawvm.finland.op_provenance import Recovered
from lawvm.finland.ops import ResolvedOp

# ---------------------------------------------------------------------------
# Registered finding codes (see core/observation_registry.py FINDING_REGISTRY).
# The literal constants live here at the production emit site so the
# registry/producer-consistency gate finds a real producer for each code.
# ---------------------------------------------------------------------------

# LS-05 (non-blocking observation): a landed op reached apply with no scope-
# resolution witness (empty ScopeConfidence).
SCOPE_CONFIDENCE_TOTALITY_GAP_AT_OP_CODE = "APPLY.SCOPE_CONFIDENCE_TOTALITY_GAP_AT_OP"
# LS-06 (non-blocking observation): the op's resolved action family differs from
# its originally-parsed action with no named conversion witness.
VERB_CONVERSION_UNWITNESSED_AT_OP_CODE = "LOWER.VERB_CONVERSION_UNWITNESSED_AT_OP"
# LS-07 (strict-blocking): a descendant-granularity op landed a write touching its
# host whole-unit.
GRANULARITY_ESCALATION_AT_OP_CODE = "APPLY.GRANULARITY_ESCALATION_AT_OP"
# LS-09 (non-blocking observation closure): a landed write touched a strict
# ancestor (parent container) of the op's claimed target.
PAYLOAD_SMUGGLING_AT_OP_CODE = "APPLY.PAYLOAD_SMUGGLING_AT_OP"
# LS-10 (non-blocking observation closure): a target address-key delta (nominal →
# resolved) with no migration/lineage event or typed rekey witness.
UNSTATED_MIGRATION_AT_OP_CODE = "APPLY.UNSTATED_MIGRATION_AT_OP"
# EV-06 (strict-blocking): a per-op ExecutionAuthorization that CITES an evidence
# policy id which is not in the known/pinned policy set.
UNKNOWN_ATTESTATION_POLICY_CODE = "EVID.UNKNOWN_ATTESTATION_POLICY"


def _cited_policy_id(authorization: ExecutionAuthorization) -> str:
    """The evidence-policy id an authorization cites, or "" if it cites none.

    The apply-path authorizations minted by ``_resolve_op_execution_authorization``
    do NOT cite a kernel evidence policy (they carry an op-id rule id and proof-
    name strings only), so this returns "" for them — the EV-06 gate is then a
    no-op on the production corpus (0-delta). An authorization projected from an
    EvidenceKernel result carries ``detail.evidence_kernel.policy_id``; that is the
    cited policy id EV-06 validates against the known set.
    """
    detail = authorization.detail
    if not isinstance(detail, Mapping):
        return ""
    kernel = detail.get("evidence_kernel")
    if isinstance(kernel, Mapping):
        return str(kernel.get("policy_id") or "")
    return ""


def gate_unknown_attestation_policy(
    *,
    authorization: ExecutionAuthorization,
    known_policy_ids: frozenset[str],
    is_strict: bool,
    source_statute: str,
    op_id: str,
    findings_out: list[Finding],
) -> None:
    """EV-06: reject an ExecutionAuthorization citing an unknown policy id.

    Validates the authorization's CITED evidence-policy id (if any) against the
    ``known_policy_ids`` set. An authorization that cites no policy id, or whose
    cited id is in the known set, passes. An authorization that cites a policy id
    absent from the known set is an attestation-policy gap: under strict it
    BLOCKS. The apply-path authorizations cite no policy id, so the production
    corpus is 0-delta; the synthetic guard-liveness drill forges a cited unknown
    policy id to exercise the gate.
    """
    cited = _cited_policy_id(authorization)
    if not cited or cited in known_policy_ids:
        return
    if not is_strict:
        return
    findings_out.append(
        Finding(
            kind=UNKNOWN_ATTESTATION_POLICY_CODE,
            role="violation",
            stage="apply",
            blocking=True,
            source_statute=source_statute,
            detail={
                "message": (
                    "An ExecutionAuthorization reaching the apply gate cites an "
                    "evidence policy id that is not in the known/pinned policy set "
                    "(attestation-policy gap, EV-06)."
                ),
                "op_id": op_id,
                "cited_policy_id": cited,
                "known_policy_ids": sorted(known_policy_ids),
                "authorization_rule_id": authorization.authorization_rule_id,
            },
        )
    )


def run_per_op_closure_sweeps(
    *,
    rop: ResolvedOp,
    is_strict: bool,
    source_statute: str,
    findings_out: list[Finding],
    migration_ledger: Optional[MigrationLedger],
) -> None:
    """Run all wave-2 per-op closure sweeps for one landed write.

    Called from ``_enforce_per_op_apply_authority`` after a write landed (the
    caller has already verified ``prev_state.ir is not new_state.ir``), so each
    sweep reasons over an op that genuinely mutated the legal-state tree.
    """
    _sweep_scope_confidence_totality(
        rop=rop, source_statute=source_statute, findings_out=findings_out
    )
    _sweep_verb_conversion_witness(
        rop=rop, source_statute=source_statute, findings_out=findings_out
    )
    _sweep_granularity_escalation(
        rop=rop, is_strict=is_strict, source_statute=source_statute,
        findings_out=findings_out,
    )
    _sweep_payload_smuggling(
        rop=rop, source_statute=source_statute, findings_out=findings_out
    )
    _sweep_unstated_migration(
        rop=rop, source_statute=source_statute, findings_out=findings_out,
        migration_ledger=migration_ledger,
    )


# ---------------------------------------------------------------------------
# LS-05 — scope-confidence totality
# ---------------------------------------------------------------------------


def _sweep_scope_confidence_totality(
    *,
    rop: ResolvedOp,
    source_statute: str,
    findings_out: list[Finding],
) -> None:
    """LS-05: every landed op records HOW its scope was obtained, or is swept."""
    if rop.resolved_scope_confidence is not None:
        return
    findings_out.append(
        Finding(
            kind=SCOPE_CONFIDENCE_TOTALITY_GAP_AT_OP_CODE,
            role="observation",
            stage="apply",
            blocking=False,
            source_statute=source_statute,
            detail={
                "message": (
                    "A state-mutating op landed with no typed scope-resolution "
                    "witness (empty ScopeConfidence); scope-confidence totality "
                    "records every op's scope provenance (LS-05)."
                ),
                "op_id": rop.op_id or "",
                "target_unit_kind": str(rop.target_unit_kind or ""),
                "target_norm": str(rop.target_norm or ""),
            },
        )
    )


# ---------------------------------------------------------------------------
# LS-06 — action-family conversion witness totality
# ---------------------------------------------------------------------------


def _parsed_action_family(rop: ResolvedOp) -> Optional[str]:
    """The originally-parsed action family for the op, as a normalized family.

    Prefer the typed core ``LegalOperation.action`` (the strongest carrier of the
    originally-parsed verb); fall back to the AmendmentOp ``op_type``. Returns the
    normalized occupancy-action family for comparison with the resolved family.
    """
    amendment_op = rop.op
    legal_op = amendment_op.lo if amendment_op is not None else None
    if legal_op is not None and legal_op.action is not None:
        # core.semantic_types.StructuralAction value is the parsed verb family.
        return str(legal_op.action.value).upper()
    if amendment_op is not None and amendment_op.op_type:
        return str(amendment_op.op_type).upper()
    return None


def _resolved_action_family(rop: ResolvedOp) -> Optional[str]:
    resolved = rop.resolved_action_type
    return str(resolved).upper() if resolved else None


def _families_differ(parsed: Optional[str], resolved: Optional[str]) -> bool:
    """True iff the parsed and resolved action families are a genuine conversion.

    Both must be present and map to DIFFERENT occupancy-action families. The
    occupancy map normalizes synonyms (e.g. TEXT_REPLACE / REPLACE both lower to
    the replace family), so this does not over-flag a benign vocabulary alias.
    """
    if not parsed or not resolved or parsed == resolved:
        return False
    parsed_action = _OP_TYPE_TO_ACTION.get(parsed)
    resolved_action = _OP_TYPE_TO_ACTION.get(resolved)
    if parsed_action is not None and parsed_action == resolved_action:
        return False
    return True


def _has_conversion_witness(rop: ResolvedOp) -> bool:
    """A named conversion witness is any extraction/target-guessing provenance tag.

    The named verb-conversion findings (LOWER.BODY_CHAPTER_REPLACE_TO_INSERT_MOVE,
    PARSE.SEMANTIC_COLLAPSE_MOVE_RENUMBER, …) stamp a provenance tag onto the op;
    a recovery-flagged op (uncovered-body / fallback) is likewise a witnessed
    non-source-authored move. Any such tag means the conversion is traceable.
    """
    return bool(
        rop.extraction_provenance_tags
        or rop.target_guessing_provenance_tags
        or rop.scope_provenance_tags
        or isinstance(rop.provenance, Recovered)
        or rop.uses_uncovered_body_recovery
        or rop.witness_rule_id
    )


def _sweep_verb_conversion_witness(
    *,
    rop: ResolvedOp,
    source_statute: str,
    findings_out: list[Finding],
) -> None:
    """LS-06: a verb conversion with no named witness is swept against."""
    parsed = _parsed_action_family(rop)
    resolved = _resolved_action_family(rop)
    if not _families_differ(parsed, resolved):
        return
    if _has_conversion_witness(rop):
        return
    findings_out.append(
        Finding(
            kind=VERB_CONVERSION_UNWITNESSED_AT_OP_CODE,
            role="observation",
            stage="apply",
            blocking=False,
            source_statute=source_statute,
            detail={
                "message": (
                    "A landed op's resolved action family differs from its "
                    "originally-parsed action with no named conversion witness; "
                    "every verb conversion must keep original intent traceable "
                    "(LS-06)."
                ),
                "op_id": rop.op_id or "",
                "parsed_action": parsed or "",
                "resolved_action": resolved or "",
            },
        )
    )


# ---------------------------------------------------------------------------
# LS-07 — granularity-escalation per-op (strict-blocking)
# ---------------------------------------------------------------------------


def _op_declares_descendant_granularity(rop: ResolvedOp) -> bool:
    """True iff the op was DECLARED (parsed) at a descendant slot granularity.

    Reads the DECLARED AmendmentOp target fields (not the resolved-address-derived
    ``effective_*`` accessors, which only reflect the post-rebind address): a
    paragraph / item / subitem / special-facet declared target is a descendant of
    the whole section/chapter unit it lives under; such an op must not overwrite
    its host.
    """
    amendment_op = rop.op
    if amendment_op is None:
        return False
    return (
        amendment_op.target_cols.target_paragraph is not None
        or bool(amendment_op.target_cols.target_item)
        or bool(amendment_op.target_cols.target_subitem)
        or bool(amendment_op.target_cols.target_special)
    )


def _sweep_granularity_escalation(
    *,
    rop: ResolvedOp,
    is_strict: bool,
    source_statute: str,
    findings_out: list[Finding],
) -> None:
    """LS-07: a descendant op whose resolved address LOST its descendant slot.

    A descendant-granularity op (declared at paragraph/item/special granularity)
    whose RESOLVED target address has no descendant step left under the host unit
    has escalated to overwrite its whole host — the host whole-unit, not the
    declared descendant slot, is what the apply would replace. Strict-only
    blocking gate (the bench/corpus replay runs permissive).
    """
    if not is_strict:
        return
    if not _op_declares_descendant_granularity(rop):
        return
    address = rop.resolved_target_address
    if address is None or not address.path:
        # No resolved address to reason over: the boundary gate (LS-01) and the
        # execution-authorization closure (EV-05) already police this op.
        return
    has_descendant_step = any(
        kind in ("subsection", "item", "subitem") for kind, _label in address.path
    ) or address.special is not None
    if has_descendant_step:
        return
    amendment_op = rop.op
    findings_out.append(
        Finding(
            kind=GRANULARITY_ESCALATION_AT_OP_CODE,
            role="violation",
            stage="apply",
            blocking=True,
            source_statute=source_statute,
            detail={
                "message": (
                    "Strict granularity gate blocked a descendant-granularity op "
                    "whose resolved target address carries no descendant slot — it "
                    "would overwrite its host whole-unit (LS-07)."
                ),
                "op_id": rop.op_id or "",
                "declared_paragraph": amendment_op.target_cols.target_paragraph if amendment_op else None,
                "declared_item": (amendment_op.target_cols.target_item or "") if amendment_op else "",
                "declared_special": (amendment_op.target_cols.target_special or "") if amendment_op else "",
                "resolved_path": [
                    [str(kind), str(label)] for kind, label in address.path
                ],
            },
        )
    )


# ---------------------------------------------------------------------------
# LS-09 — payload-smuggling closure (parent-container smuggle)
# ---------------------------------------------------------------------------


def _sweep_payload_smuggling(
    *,
    rop: ResolvedOp,
    source_statute: str,
    findings_out: list[Finding],
) -> None:
    """LS-09: a claim on a descendant never authorizes its parent container.

    The per-op mutation-boundary gate (LS-01) already checks the changed paths
    are a SUBSET of the target boundary. The smuggle closure is the dual: when
    the op declares a descendant target (paragraph/item) but its resolved address
    was WIDENED to the bare host unit (no descendant step), an admitted write
    could touch the parent container the op never claimed. This records the
    widen-to-parent as a non-blocking closure observation (the boundary gate
    blocks the actual escape under strict; this surfaces the structural smuggle
    risk for ANY profile, tag-don't-guess).
    """
    amendment_op = rop.op
    declared_descendant = amendment_op is not None and (
        amendment_op.target_cols.target_paragraph is not None
        or bool(amendment_op.target_cols.target_item)
        or bool(amendment_op.target_cols.target_subitem)
    )
    if not declared_descendant:
        return
    address = rop.resolved_target_address
    if address is None or not address.path:
        return
    has_descendant_step = any(
        kind in ("subsection", "item", "subitem") for kind, _label in address.path
    )
    if has_descendant_step:
        return
    findings_out.append(
        Finding(
            kind=PAYLOAD_SMUGGLING_AT_OP_CODE,
            role="observation",
            stage="apply",
            blocking=False,
            source_statute=source_statute,
            detail={
                "message": (
                    "A descendant-claiming op resolved to its bare host unit with "
                    "no descendant step; admitting the write could touch the parent "
                    "container the op never claimed (payload-smuggling closure, "
                    "LS-09)."
                ),
                "op_id": rop.op_id or "",
                "declared_paragraph": amendment_op.target_cols.target_paragraph if amendment_op else None,
                "declared_item": (amendment_op.target_cols.target_item or "") if amendment_op else "",
                "resolved_path": [
                    [str(kind), str(label)] for kind, label in address.path
                ],
            },
        )
    )


# ---------------------------------------------------------------------------
# LS-10 — unstated-migration closure
# ---------------------------------------------------------------------------


def _nominal_target_address(rop: ResolvedOp) -> Optional[LegalAddress]:
    """The originally-parsed (nominal) target address, before late-waist rebind."""
    amendment_op = rop.op
    legal_op = amendment_op.lo if amendment_op is not None else None
    if legal_op is not None and legal_op.target is not None:
        return legal_op.target
    return None


def _address_key(address: Optional[LegalAddress]) -> tuple[tuple[str, str], ...]:
    if address is None or not address.path:
        return ()
    return tuple((str(kind), str(label)) for kind, label in address.path)


def _migration_backs_delta(
    rop: ResolvedOp,
    migration_ledger: Optional[MigrationLedger],
) -> bool:
    """A migration/lineage event or a typed rekey witness backs the address delta.

    A scope-rewrite / carry-forward scope witness, a same-wave migration tag, or a
    migration ledger that resolves the op's target are all typed backings; an op
    flagged with a scope provenance tag has a recorded reason for its rebind.
    """
    if migration_ledger is not None and bool(migration_ledger):
        return True
    return bool(
        rop.scope_provenance_tags
        or rop.target_guessing_provenance_tags
        or rop.witness_rule_id
        or rop.body_chapter_move_from is not None
        or (rop.resolved_scope_confidence is not None
            and rop.resolved_scope_confidence.fallback_reason is not None)
    )


def _sweep_unstated_migration(
    *,
    rop: ResolvedOp,
    source_statute: str,
    findings_out: list[Finding],
    migration_ledger: Optional[MigrationLedger],
) -> None:
    """LS-10: an address-key delta is backed by a migration/rekey witness."""
    nominal = _address_key(_nominal_target_address(rop))
    resolved = _address_key(rop.resolved_target_address)
    if not nominal or not resolved or nominal == resolved:
        # No nominal address to compare, or no address-key delta to explain.
        return
    if _migration_backs_delta(rop, migration_ledger):
        return
    findings_out.append(
        Finding(
            kind=UNSTATED_MIGRATION_AT_OP_CODE,
            role="observation",
            stage="apply",
            blocking=False,
            source_statute=source_statute,
            detail={
                "message": (
                    "A state-mutating op's resolved target address differs from "
                    "its nominal (parsed) address with no migration/lineage event "
                    "or typed rekey witness (unstated-migration closure, LS-10)."
                ),
                "op_id": rop.op_id or "",
                "nominal_path": [[k, v] for k, v in nominal],
                "resolved_path": [[k, v] for k, v in resolved],
            },
        )
    )
