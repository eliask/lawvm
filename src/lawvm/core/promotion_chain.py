"""Promotion-chain integrity primitives (audit-registry CHAIN-/PROMOTE- families).

§0 promotion-chain principle: a node earns the right to mutate replay only by
climbing the boundary

    source witness -> candidate claim -> execution-authorization
    -> dry-run/replay proof -> agreement/adjudication row

— never by accumulation. The per-link audits already exist (EV-05 the
execution-authorization closure, OV-01/02 the overlay-promotion closure, EV-09
the build-consumption + retraction-taint query). This module hosts the
*end-to-end* primitives that reason over the chain AS A WHOLE for a single
mutating op:

* **PROMOTE-02 — authorization scope-match** (the concrete, fully-checkable
  deliverable): an :class:`~lawvm.core.execution_authorization.ExecutionAuthorization`
  authorizes EXACTLY the op whose derived identity it was minted for. Reusing an
  authorization minted for op A to gate a different op B (same rule family,
  different op) is smuggled authority (§1.5 authority analogue / §8 derived-object
  identity). The fully-checkable invariant today is that the authorization's bound
  ``authorization_rule_id`` equals the op's derived ``rule_id``. See the
  CARRIER HONESTY note below for the identity components NOT yet carried.

* **CHAIN-02 — promotion-chain monotonicity**: no link is reached without its
  predecessor (no execution-authorization without a candidate claim; no replay
  proof without execution-authorization). Checked structurally over a typed
  :class:`PromotionChainLinks` snapshot of which links are present.

* **CHAIN-01 — promotion-chain completeness**: every link present, none skipped.
  The integral of EV-05 / OV-01 / EV-09. Checked over the same typed snapshot.

CARRIER HONESTY (the stated PART residual — do NOT overclaim):
    Today's apply-path ExecutionAuthorization (minted by
    ``finland.apply_resolved_op._resolve_op_execution_authorization``) binds ONLY
    ``authorization_rule_id = op_id``. It carries NO ``input_node_ids``,
    ``policy_id``, or ``candidate_set_hash`` for an op-level subject. Therefore:
    - PROMOTE-02's rule_id<->op_id binding IS checkable (implemented here).
    - PROMOTE-02's deeper (input_node_ids, policy_id, candidate_set_hash) binding
      is NOT checkable — the carrier does not exist. ``derive_op_authority_identity``
      surfaces the op-side components that DO exist (rule_id + target address key +
      action family) so the residual is named and bounded, not invented; the
      AUTHORIZATION side does not yet bind them.
    - CHAIN-01/CHAIN-02 reason over a :class:`PromotionChainLinks` snapshot whose
      ``candidate_claim`` / ``dry_run_proof`` / ``agreement_row`` links are NOT
      materialized as typed apply-path carriers today; the apply path carries the
      execution-authorization link (EV-05) and the source-witness link (op_id +
      source statute). Completeness over the FULL 5-link chain is therefore PART:
      the present-link assertion is exact for the links that exist, and the
      missing links are named on the snapshot, never silently assumed present.
    - PROMOTE-01 sub-chain retraction propagation: EV-09 propagates a retraction
      to its IMMEDIATE consuming build (one hop). Multi-hop sub-chain propagation
      (build -> downstream build -> ...) needs a typed inter-link consumption edge
      the apply path does not yet emit; ``downchain_links_reopened`` checks the
      one-hop arm that EV-09 supports and reports the multi-hop residual.

Nothing here mutates state or branches the production apply path. These are pure
functions over already-computed carriers (the audit gates / tests call them).
"""

from __future__ import annotations

from dataclasses import dataclass

from lawvm.core.execution_authorization import ExecutionAuthorization

# ---------------------------------------------------------------------------
# Derived-object identity (§8) for an op-level execution authorization subject
# ---------------------------------------------------------------------------

# The ordered §0 promotion-chain links. ``source_witness`` is the floor (a
# source statute + op identity); ``agreement_row`` is the apex. Monotonicity
# (CHAIN-02) walks this order; completeness (CHAIN-01) requires the whole set.
PROMOTION_CHAIN_LINKS: tuple[str, ...] = (
    "source_witness",
    "candidate_claim",
    "execution_authorization",
    "dry_run_proof",
    "agreement_row",
)


@dataclass(frozen=True, slots=True)
class DerivedOpAuthorityIdentity:
    """Derived identity (§8) of the op an execution-authorization gates.

    ``rule_id`` is the op's stable identity (its ``op_id``) — the ONLY component
    the apply-path ExecutionAuthorization binds today, so the only component a
    scope-match can check against the authorization. ``input_node_ids`` and
    ``action_family`` are the op-side identity carriers that DO exist on the
    resolved op; they are surfaced here so the deeper scope-match residual is
    named and bounded — they are NOT bound on the authorization yet, so a check
    against them is the stated PART, not a fabricated gate.
    """

    rule_id: str
    input_node_ids: tuple[str, ...] = ()
    action_family: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "rule_id", str(self.rule_id or ""))
        object.__setattr__(
            self, "input_node_ids", tuple(str(n) for n in self.input_node_ids)
        )
        object.__setattr__(self, "action_family", str(self.action_family or ""))


def derive_op_authority_identity(
    *,
    op_id: str,
    target_address_key: tuple[tuple[str, str], ...] = (),
    action_family: str = "",
) -> DerivedOpAuthorityIdentity:
    """Derive the §8 identity of the op a per-op authorization gates.

    The ``rule_id`` is the op's ``op_id`` — what the apply-path authorization
    binds. ``input_node_ids`` is rendered from the resolved target address path
    (the node(s) the op writes); ``action_family`` is the resolved action. These
    last two exist on the op but are not bound on the authorization (the residual).
    """
    input_node_ids = tuple(
        f"{kind}:{label}" for kind, label in target_address_key if kind
    )
    return DerivedOpAuthorityIdentity(
        rule_id=str(op_id or "").strip(),
        input_node_ids=input_node_ids,
        action_family=str(action_family or ""),
    )


@dataclass(frozen=True, slots=True)
class AuthorizationScopeMatch:
    """Result of the PROMOTE-02 authorization scope-match check."""

    matched: bool
    bound_rule_id: str
    derived_rule_id: str
    reason: str = ""
    # The identity components that exist op-side but are NOT bound on the
    # authorization today — the stated, bounded PART residual.
    unbound_identity_components: tuple[str, ...] = ()


# Identity components that PROMOTE-02 would bind in full but that the apply-path
# ExecutionAuthorization does not carry today (the named, bounded residual).
PROMOTE02_UNBOUND_IDENTITY_COMPONENTS: tuple[str, ...] = (
    "input_node_ids",
    "policy_id",
    "candidate_set_hash",
)


def check_authorization_scope_match(
    authorization: ExecutionAuthorization,
    derived: DerivedOpAuthorityIdentity,
) -> AuthorizationScopeMatch:
    """PROMOTE-02: an authorization gates EXACTLY the op it was minted for.

    The fully-checkable invariant today: the authorization's bound
    ``authorization_rule_id`` MUST equal the op's derived ``rule_id`` (its
    ``op_id``). Reusing an authorization minted for op A to gate op B is a
    rule_id mismatch — smuggled authority. The deeper (input_node_ids, policy_id,
    candidate_set_hash) binding is the stated residual: those components are not
    carried on the authorization, so they are reported as unbound rather than
    silently treated as matching.
    """
    bound = str(authorization.authorization_rule_id or "")
    derived_rule_id = str(derived.rule_id or "")
    if not bound:
        return AuthorizationScopeMatch(
            matched=False,
            bound_rule_id=bound,
            derived_rule_id=derived_rule_id,
            reason="authorization binds no rule_id; cannot establish scope",
            unbound_identity_components=PROMOTE02_UNBOUND_IDENTITY_COMPONENTS,
        )
    if not derived_rule_id:
        return AuthorizationScopeMatch(
            matched=False,
            bound_rule_id=bound,
            derived_rule_id=derived_rule_id,
            reason="op carries no derived rule_id (op_id) to bind authorization to",
            unbound_identity_components=PROMOTE02_UNBOUND_IDENTITY_COMPONENTS,
        )
    if bound != derived_rule_id:
        return AuthorizationScopeMatch(
            matched=False,
            bound_rule_id=bound,
            derived_rule_id=derived_rule_id,
            reason=(
                "authorization bound rule_id does not equal the op's derived "
                "identity rule_id — authority minted for a different op "
                "(smuggled authority, PROMOTE-02 / §1.5 authority analogue)"
            ),
            unbound_identity_components=PROMOTE02_UNBOUND_IDENTITY_COMPONENTS,
        )
    return AuthorizationScopeMatch(
        matched=True,
        bound_rule_id=bound,
        derived_rule_id=derived_rule_id,
        reason="",
        unbound_identity_components=PROMOTE02_UNBOUND_IDENTITY_COMPONENTS,
    )


# ---------------------------------------------------------------------------
# CHAIN-01 / CHAIN-02 — completeness + monotonicity over a typed link snapshot
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PromotionChainLinks:
    """A typed snapshot of which §0 promotion-chain links are present for one op.

    Each flag asserts the corresponding link is MATERIALIZED as a typed carrier.
    A flag whose link is not representable as an apply-path carrier today is
    listed in ``unmaterialized_links`` so completeness over those links is a
    NAMED residual, never silently assumed present.
    """

    source_witness: bool
    candidate_claim: bool
    execution_authorization: bool
    dry_run_proof: bool
    agreement_row: bool
    # Links the apply path cannot represent as a typed carrier yet (the bounded
    # CHAIN-01 PART residual). Completeness is asserted ONLY over the links not
    # listed here.
    unmaterialized_links: tuple[str, ...] = ()

    def _link_flags(self) -> dict[str, bool]:
        return {
            "source_witness": self.source_witness,
            "candidate_claim": self.candidate_claim,
            "execution_authorization": self.execution_authorization,
            "dry_run_proof": self.dry_run_proof,
            "agreement_row": self.agreement_row,
        }

    def present(self, link: str) -> bool:
        return bool(self._link_flags().get(link, False))

    def materialized_links(self) -> tuple[str, ...]:
        unmaterialized = set(self.unmaterialized_links)
        return tuple(
            link for link in PROMOTION_CHAIN_LINKS if link not in unmaterialized
        )


@dataclass(frozen=True, slots=True)
class PromotionChainVerdict:
    """Result of a CHAIN-01/CHAIN-02 check over a :class:`PromotionChainLinks`."""

    complete: bool
    monotone: bool
    missing_links: tuple[str, ...] = ()
    accumulation_links: tuple[str, ...] = ()
    unmaterialized_links: tuple[str, ...] = ()
    reason: str = ""


def check_promotion_chain(links: PromotionChainLinks) -> PromotionChainVerdict:
    """CHAIN-01 (completeness) + CHAIN-02 (monotonicity) over the materialized links.

    Completeness (CHAIN-01): every MATERIALIZED link is present. Links named in
    ``unmaterialized_links`` are excluded from the completeness requirement (the
    bounded PART residual) but reported so the gap is visible.

    Monotonicity (CHAIN-02): no materialized link is present while a materialized
    PREDECESSOR link (earlier in :data:`PROMOTION_CHAIN_LINKS`) is absent —
    authority acquired by accumulation rather than by climbing the boundary is a
    typed §0 violation ("never by accumulation"). A predecessor that is itself
    unmaterialized does not gate its successor (it is a named residual, not a
    monotonicity break).
    """
    materialized = links.materialized_links()
    materialized_set = set(materialized)

    missing = tuple(link for link in materialized if not links.present(link))
    complete = not missing

    accumulation: list[str] = []
    seen_present = True
    for link in PROMOTION_CHAIN_LINKS:
        if link not in materialized_set:
            # Unmaterialized predecessor cannot gate downstream links; it is a
            # named residual, so treat it as transparent to monotonicity.
            continue
        present = links.present(link)
        if present and not seen_present:
            # A present link whose materialized predecessor was absent: authority
            # was reached without climbing the predecessor (accumulation).
            accumulation.append(link)
        # Carry the predecessor-presence state forward only across materialized
        # links; once a materialized link is absent, any later present link is an
        # accumulation break.
        if not present:
            seen_present = False
    monotone = not accumulation

    reasons: list[str] = []
    if missing:
        reasons.append(
            "promotion chain incomplete: missing materialized link(s) "
            f"{list(missing)} (CHAIN-01)"
        )
    if accumulation:
        reasons.append(
            "promotion chain acquired authority by accumulation: link(s) "
            f"{accumulation} present with an absent materialized predecessor "
            "(CHAIN-02, never by accumulation)"
        )
    return PromotionChainVerdict(
        complete=complete,
        monotone=monotone,
        missing_links=missing,
        accumulation_links=tuple(accumulation),
        unmaterialized_links=tuple(links.unmaterialized_links),
        reason="; ".join(reasons),
    )


# ---------------------------------------------------------------------------
# PROMOTE-01 — retraction propagates down-chain (one-hop arm + named residual)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DownchainRetractionVerdict:
    """Result of the PROMOTE-01 down-chain retraction-propagation check.

    ``immediate_consumers_reopened`` is the EV-09-supported one-hop arm: every
    build that DIRECTLY consumed a retracted assertion is tainted/reopened.
    ``multi_hop_residual`` names the sub-chain depth PROMOTE-01 would cover in
    full but that the apply path cannot represent today (no typed inter-link
    consumption edge beyond the immediate consumer), so it is reported, never
    silently assumed propagated.
    """

    immediate_consumers_reopened: bool
    stale_downstream: tuple[str, ...] = ()
    multi_hop_residual: str = ""
    reason: str = ""


# The named residual for PROMOTE-01: EV-09 propagates a retraction one hop (to
# the immediate consuming build). Multi-hop sub-chain propagation needs a typed
# inter-link consumption edge the apply path does not emit yet.
PROMOTE01_MULTI_HOP_RESIDUAL: str = (
    "EV-09 propagates retraction to the immediate consuming build only "
    "(one hop); multi-hop sub-chain propagation (build -> downstream build "
    "-> ...) is unrepresentable without a typed inter-link consumption edge "
    "the apply path does not emit yet"
)


def check_downchain_retraction_reopened(
    *,
    retracted_link: str,
    downstream_links: tuple[str, ...],
    reopened_links: frozenset[str],
) -> DownchainRetractionVerdict:
    """PROMOTE-01: every immediate downstream link of a retracted link is reopened.

    ``downstream_links`` are the links built ON the retracted link;
    ``reopened_links`` are those EV-09 (or an equivalent taint query) has marked
    reopened/tainted. A downstream link standing on the retracted predecessor
    without being reopened is stale-on-a-withdrawn-predecessor. The multi-hop
    sub-chain residual is named on the verdict.
    """
    stale = tuple(
        link for link in downstream_links if link not in reopened_links
    )
    immediate_ok = not stale
    reason = ""
    if stale:
        reason = (
            f"retracted link {retracted_link!r} has downstream link(s) {list(stale)} "
            "left standing without reopen/taint (PROMOTE-01 stale-downstream)"
        )
    return DownchainRetractionVerdict(
        immediate_consumers_reopened=immediate_ok,
        stale_downstream=stale,
        multi_hop_residual=PROMOTE01_MULTI_HOP_RESIDUAL,
        reason=reason,
    )


# Re-export for callers that want the typed link order without the dataclass.
__all__ = [
    "PROMOTION_CHAIN_LINKS",
    "DerivedOpAuthorityIdentity",
    "derive_op_authority_identity",
    "AuthorizationScopeMatch",
    "PROMOTE02_UNBOUND_IDENTITY_COMPONENTS",
    "check_authorization_scope_match",
    "PromotionChainLinks",
    "PromotionChainVerdict",
    "check_promotion_chain",
    "DownchainRetractionVerdict",
    "PROMOTE01_MULTI_HOP_RESIDUAL",
    "check_downchain_retraction_reopened",
]
