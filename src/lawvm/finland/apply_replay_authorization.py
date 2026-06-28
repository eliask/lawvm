"""FI apply/replay execution-authorization projection (StageResult endgame WAIST #7).

The deepest authority boundary: the FI replay/apply path applies writes by
CONVENTION today (a permissive ``StrictProfile`` + "we just applied" + nothing
blocked). This module makes that convention an EXPLICIT, type-carried
:class:`~lawvm.core.execution_authorization.ExecutionAuthorization` wrapped in an
:class:`~lawvm.core.stage_result.AuthoritySurface` — the authority firewall in
the type (``pro_on_architectural_coherence.md`` §8).

DESCRIPTIVE, NOT NORMATIVE (the Audit-D move). The mapping mints
``replay_authorized=True`` ⟺ the EXACT conjunction that already lets a write
stand today:

  * the op landed (``disposition == "APPLIED"``), AND
  * no blocking structural mutation-boundary residual (WAIST #3 — an unexplained
    bound→landed divergence, ``WriteReceipt.divergence_explained is False``), AND
  * a clean observed-vs-declared cross-check (no undeclared mutation touch).

It NEVER loosens the gate: a permissive strict profile is named as a forbidden
shortcut, NOT treated as replay authority. The neutral (un-granted) surface is
``replay_authorized=False`` by construction.

The per-op surface is carried on ``apply_resolved_op_staged``'s
``StageResult[ReplayState]``; the per-replay aggregate (AND over every landed
write — one unauthorized write un-authorizes the whole replay) is the
clean-claim predicate the certificate dossier branches on (the firewall bite).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional, Sequence

from lawvm.core.execution_authorization import ExecutionAuthorization
from lawvm.core.stage_result import AuthoritySurface, NEUTRAL_AUTHORITY
from lawvm.core.write_receipt import DivergenceKind, _paths_consistent_under_prefix

if TYPE_CHECKING:
    from lawvm.core.phase_result import Finding
    from lawvm.core.write_receipt import WriteReceipt

logger = logging.getLogger(__name__)


# The registered apply-authority rule id (ESCALATE-5W: descriptive registration
# in the same vocabulary the other ExecutionAuthorization rule ids use). It names
# the gate facts; it does not introduce a new normative threshold.
FI_APPLY_REPLAY_AUTHORIZATION_RULE_ID = "fi_apply_replay_authorization"

# The named anti-patterns this waist closes (the firewall's forbidden shortcuts).
APPLY_REPLAY_FORBIDDEN_SHORTCUTS: tuple[str, ...] = (
    "strict_profile_permissiveness_as_replay_authority",
    "write_receipt_existence_as_replay_authority",
)

# The proofs a non-authorized apply must supply before replay authority is granted.
APPLY_REPLAY_REQUIRED_PROOFS: tuple[str, ...] = (
    "mutation_boundary_divergence_explained",
    "observed_writes_fully_declared",
)

_APPLY_REPLAY_SAFE_DEFAULT = "block_until_apply_replay_gate_is_satisfied"

# The blocking finding code the apply boundary already emits for an undeclared
# mutation touch / an unexplained container boundary divergence (#3/the undeclared
# touch cross-check). Its presence on a replay's findings ledger is the
# already-load-bearing signal that a landed write touched outside its declared
# footprint — i.e. the write does NOT stand under the conservative gate.
APPLY_BOUNDARY_VIOLATION_FINDING_CODE = "REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET"
#: Same code under the apply-producer's name. ``apply_resolved_op`` imports this
#: alias instead of defining its own literal, so the producer (write-receipt
#: firewall) and the authority consumer can never silently drift apart.
WRITE_RECEIPT_VIOLATION_FINDING_CODE = APPLY_BOUNDARY_VIOLATION_FINDING_CODE


def mint_apply_replay_authority(
    *,
    replay_authorized: bool,
) -> AuthoritySurface:
    """Mint the per-op/per-replay apply :class:`AuthoritySurface`.

    The single descriptive mapping from the gate predicate to a typed
    :class:`ExecutionAuthorization`. ``replay_authorized`` is the EXACT
    conjunction that already lets the write(s) stand (see module docstring); this
    function only PROJECTS that fact into the typed authority shape, satisfying
    the :func:`validate_execution_authorization` rules
    (``replay_authorized`` requires ``executable``; a non-authorized row must list
    ``required_proofs``; ``safe_default`` required).
    """
    executable = bool(replay_authorized)
    return AuthoritySurface(
        authorization=ExecutionAuthorization(
            executable=executable,
            replay_authorized=bool(replay_authorized),
            authorization_status=(
                "replay_authorized" if replay_authorized else "apply_replay_gate_blocked"
            ),
            authorization_rule_id=FI_APPLY_REPLAY_AUTHORIZATION_RULE_ID,
            owner_phase="apply",
            strict_disposition="record" if replay_authorized else "block",
            required_proofs=() if replay_authorized else APPLY_REPLAY_REQUIRED_PROOFS,
            safe_default=_APPLY_REPLAY_SAFE_DEFAULT,
            forbidden_shortcuts=APPLY_REPLAY_FORBIDDEN_SHORTCUTS,
        )
    )


def op_replay_authorized(
    *,
    disposition: str,
    has_blocking_structural_residual: bool,
    undeclared_touch_present: bool,
) -> bool:
    """The per-op gate predicate — the exact conjunction that lets a write stand.

    ``True`` ⟺ the op landed AND no blocking structural mutation-boundary
    residual AND a clean observed-vs-declared cross-check. Any other case is a
    non-authorized apply (the write would not stand cleanly under the default
    strict profile).
    """
    return (
        disposition == "APPLIED"
        and not has_blocking_structural_residual
        and not undeclared_touch_present
    )


def _receipt_boundary_authorized(receipt: "WriteReceipt") -> bool:
    """True unless the receipt is an UNEXPLAINED bound→landed divergence.

    A receipt with no bound target (``bound_target_path is None`` — the op-level
    apply write with no resolver binding) has no divergence to explain and is
    authorized by absence of a binding. A receipt that bound a target authorizes
    iff its bound→landed divergence is explained (``divergence_explained``: bound
    == landed, or a named recovery/migration/fallback rule), OR the divergence is
    a strict prefix-of relation (PR2 receipt-prefix-equivalence). This is the
    exact condition the #3 structural mutation-boundary residual fires on.

    PR2 receipt-prefix-equivalence (``BOUND_TARGET_PATH_NORMALIZATION_DESIGN``
    §3): the receipt-boundary arm recognizes that ``bound_target_path`` is a
    strict prefix of ``landed_primary_path`` (Pattern B — bound=section-level,
    landed=subsection-level) OR vice versa (Pattern A — bound=subsection-level,
    landed=section-level). The relation is benign-by-relation-shape because the
    undeclared-touch cross-check (``no_boundary_violation`` in
    :func:`aggregate_replay_authority`) — the load-bearing independent witness
    — complains when the op's declared mutation events do not cover the deeper
    side's descendant keys. The receipt arm's prefix authorization is therefore
    DESIGNED to defer to that cross-check: a dirty cross-check refuses
    authorization at the aggregate level regardless of the prefix relation.

    The receipt's typed ``divergence_kind`` (PR2) is computed at the receipt-
    construction site (``apply_resolved_op._collect_op_write_receipt``) and
    carries the witness; the named ``APPLY.RECEIPT_BOUND_PREFIX_OF_LANDED``
    observation row carrying bound/landed is emitted at construction so the
    prefix authorization is OWNED (per §0 prime directive: an *invisible*
    heuristic is forbidden). For defense-in-depth — and to keep this arm
    reachable from constructed receipts that pre-date PR2 threading — the
    helper recomputes :func:`_paths_consistent_under_prefix` directly when the
    receipt has no typed ``divergence_kind`` set (legacy / ``None``). Both bound
    and landed paths arrive in canonical form (wrapper-strip + kind-alias
    rewrite — see ``finland._receipt_path_norm._normalize_receipt_path_for_comparison``)
    from the receipt-construction site; the prefix check passes them through
    as-is so the helper trusts the typed input per §1.12.

    HONESTY NOTE (the receipt arm is now LIVE in production): the op-level
    ``_collect_op_write_receipt`` (the only producer reaching the aggregated
    ``signals.write_receipts`` sink today) threads
    ``bound_target_path = rop.resolved_target_address.path`` (canonicalized) —
    PR1 closed the 29 Pattern-C kind-label-mismatch false-positives; PR2 (this
    arm's prefix recognition) closes the 71+15 Pattern-A/B prefix-count
    false-positives. A blocking structural residual still fires when the
    undeclared-touch cross-check (the ``no_boundary_violation`` conjunct of
    :func:`aggregate_replay_authority`) is dirty — that is the load-bearing
    independent witness for the prefix relation's benignity.
    """
    if receipt.bound_target_path is None:
        return True
    # PR2 — typed fast path: the receipt-construction site already classified
    # the relation (PREFIX_OF_LANDED, EXACT_MATCH, EXPLAINED_BY_RULE,
    # UNEXPLAINED_DIVERGENCE). Trust the typed owner (§1.12 — no semantic
    # reach-back); fall through to the legacy recomputation for pre-PR2
    # constructed receipts (``divergence_kind is None`` — defense in depth).
    if receipt.divergence_kind is DivergenceKind.PREFIX_OF_LANDED:
        return True
    if receipt.divergence_explained:
        return True
    # Legacy reach-back (defense in depth): reconstruct the prefix relation
    # when the typed witness was not set at construction. The bound/landed
    # paths on the receipt are ALREADY in canonical form; the helper passes
    # them through without re-normalizing.
    bound = receipt.bound_target_path
    landed = receipt.landed_primary_path
    if bound is not None and landed is not None and _paths_consistent_under_prefix(bound, landed):
        return True
    return False


class ObservationPromotedToAuthorityError(AssertionError):
    """A role=="observation" finding entered the apply-path authority source set.

    EV-04: observations explain authority; they never become authority by
    existing. The apply-path authority source set is the BLOCKING findings only.
    A role=="observation" finding is structurally non-blocking, so this should be
    unreachable — it is asserted loudly so a future refactor that lets an
    observation gate authority fails fast instead of silently promoting evidence.
    """


def _apply_authority_relevant_findings(
    findings: "Sequence[Finding]",
) -> tuple["Finding", ...]:
    """Return the authority-relevant (blocking) findings; reject observation promotion.

    EV-04 closure: the apply-path authority source set is exactly the BLOCKING
    findings. Any ``role == "observation"`` finding that is also blocking is an
    observation-promoted-to-authority defect and fails loud.
    """
    relevant: list["Finding"] = []
    for finding in findings:
        if not finding.blocking:
            continue
        if getattr(finding, "role", "") == "observation":
            raise ObservationPromotedToAuthorityError(
                "a role=='observation' finding "
                f"({finding.kind!r}) is blocking and would enter the apply-path "
                "authority source set; observations explain authority, they do not "
                "become authority (EV-04)"
            )
        relevant.append(finding)
    return tuple(relevant)


def aggregate_replay_authority(
    *,
    write_receipts: Sequence["WriteReceipt"],
    findings: Sequence["Finding"],
) -> AuthoritySurface:
    """Aggregate the per-replay apply authority over every LANDED write (1W).

    ``replay_authorized`` = AND over all landed writes' surfaces (one unauthorized
    write un-authorizes the replay). This is the per-replay clean-claim predicate
    the certificate dossier branches on, computed DESCRIPTIVELY from the same two
    landed-reality signals the cert already consumes:

      * every landed ``WriteReceipt`` carries an explained mutation boundary
        (``divergence_explained`` — the #3 structural residual fires when False),
        AND
      * no apply-boundary touch-outside-target violation finding was emitted (the
        undeclared-touch / container-divergence blocking signal already on the
        replay findings ledger).

    With zero landed writes the replay authorizes trivially (nothing to forbid) —
    the aggregate AND over an empty set is True. The green corpus lands all its
    writes with explained boundaries and a clean cross-check, so every aggregate
    is ``replay_authorized=True`` (0-delta).

    NB on ``divergence_explained``: a receipt with ``bound_target_path is None``
    carried NO resolver binding at this granularity (the op-level apply receipt —
    see ``apply_resolved_op._collect_op_write_receipt``). There is no bound→landed
    divergence to explain in that case, and the apply path does NOT block such a
    write today (the #3 structural mutation-boundary residual fires only for a
    bound target that diverged from the landed path with no named rule). Treating
    a ``bound=None`` receipt as an unexplained divergence would mark a write that
    legitimately stands today as unauthorized — that is a mapping error, NOT a
    finding. The descriptive gate therefore only un-authorizes on an UNEXPLAINED
    BOUND→LANDED DIVERGENCE: a receipt that bound a target and landed elsewhere
    with no named recovery rule.

    POST-PR2 REALITY (the op-level receipt arm IS load-bearing): the op-level
    apply receipt threads ``bound_target_path = rop.resolved_target_address.path``
    (PR1+PR2; canonicalized via
    ``finland/_receipt_path_norm._normalize_receipt_path_for_comparison``); the
    receipt-bound arm at ``_receipt_boundary_authorized:128`` consumes the typed
    ``DivergenceKind`` witness from ``receipt.divergence_kind``. Receipts built
    inside ``apply_typed_dispatch`` / ``apply_structure_ops`` remain on a local
    list and ARE NOT threaded to the aggregate sink — that additional threading
    is the explicitly-deferred follow-up.
    """
    every_receipt_explained = all(
        _receipt_boundary_authorized(receipt) for receipt in write_receipts
    )
    # EV-04 (observation-not-authority): the authority source set is exactly the
    # BLOCKING findings. A role=="observation" finding may never gate authority by
    # existing; it explains, it does not authorize. The conjunction below is
    # guarded by .blocking, and a role=="observation" finding is structurally
    # non-blocking (the registry forbids a blocking observation), so it can never
    # enter the authority set. Assert it loudly rather than rely on that invariant.
    authority_relevant = _apply_authority_relevant_findings(findings)
    no_boundary_violation = not any(
        finding.kind == APPLY_BOUNDARY_VIOLATION_FINDING_CODE
        for finding in authority_relevant
    )
    replay_authorized = every_receipt_explained and no_boundary_violation
    return mint_apply_replay_authority(replay_authorized=replay_authorized)


def aggregate_replay_authority_or_neutral(
    *,
    write_receipts: Optional[Sequence["WriteReceipt"]],
    findings: Optional[Sequence["Finding"]],
) -> AuthoritySurface:
    """Aggregate, or the NEUTRAL (un-granted) surface when no apply path ran.

    A replay that never took an apply pass carries no landed writes; rather than
    mint a vacuously-authorized surface for a path with no execution authority at
    all, callers that cannot prove an apply pass occurred get the neutral surface.
    Callers that DID run the apply path pass the receipts/findings and get the
    descriptive aggregate.
    """
    if write_receipts is None or findings is None:
        return NEUTRAL_AUTHORITY
    return aggregate_replay_authority(
        write_receipts=write_receipts, findings=findings
    )
