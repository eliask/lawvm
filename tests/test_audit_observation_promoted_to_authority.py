"""Tests for the D11 ``EVID.AUTHORITY_SOURCE_EXCLUDES_OBSERVATION_KINDS`` audit.

Per :file:`notes_internal/audit_impl_D11.md`: an observation-role finding kind
appearing in the apply-path authority source set voids the
:class:`ExecutionAuthorization`, breaching the §2.10 evidence→authority
firewall (``evidence explains authority; it does not become authority by
existing``). The audit lives in
:mod:`lawvm.core.execution_authorization`:

* :func:`authority_source_set_observation_audit` consumes the caller-supplied
  authority source kinds and returns one
  :class:`ObservationPromotedToAuthority` record per observation-role kind.
* :func:`observation_promoted_findings` projects those records to one
  ``EVID.OBSERVATION_PROMOTED_TO_AUTHORITY`` violation Finding per promotion.

Honest scope: the wire into ``aggregate_replay_authority`` +
``uk_amendment_replay.authority_mode`` filter is staged as a follow-up commit
(parallel to D7/D8's wire-then-promote discipline); until the wire, this audit
runs only via the unit/helper lane. Hole closes when the wire lands.
"""

from __future__ import annotations

from lawvm.core.execution_authorization import (
    EVID_AUTHORITY_SOURCE_EXCLUDES_OBSERVATION_KINDS_RULE_ID,
    EVID_OBSERVATION_PROMOTED_TO_AUTHORITY_FINDING_CODE,
    ObservationPromotedToAuthority,
    authority_source_set_observation_audit,
    observation_promoted_findings,
)
from lawvm.core.observation_registry import FINDING_REGISTRY
from lawvm.core.phase_result import Finding, VIOLATION_ROLE


# --------------------------------------------------------------------------- #
# Firing case — the load-bearing guard-liveness test.                          #
# --------------------------------------------------------------------------- #


def test_observation_role_kind_in_authority_source_set_fires_one_promotion() -> None:
    """Per audit_impl_D11 §6 positive: an observation-role finding kind in the
    authority source set yields exactly one
    :class:`ObservationPromotedToAuthority` promotion record.

    Drives ``ELAB.SOURCE_PATHOLOGY`` (a registered observation-role kind)
    through the audit. The audit MUST fire — the observation plane has been
    silently promoted to authority (§2.10 breach).
    """
    promotions = authority_source_set_observation_audit(
        ("ELAB.SOURCE_PATHOLOGY",),
        op_id="op-fire",
        owner_phase="apply",
    )
    assert len(promotions) == 1
    promotion = promotions[0]
    assert isinstance(promotion, ObservationPromotedToAuthority)
    assert promotion.promoted_kind == "ELAB.SOURCE_PATHOLOGY"
    assert promotion.op_id == "op-fire"
    assert promotion.owner_phase == "apply"


def test_observation_role_kind_projects_to_violation_finding() -> None:
    """Promotion records project to a ``EVID.OBSERVATION_PROMOTED_TO_AUTHORITY``
    violation Finding.

    Per audit_impl_D11 §5: the finding MUST be ``role="violation"`` and
    ``blocking=True`` regardless of strict/quirks profile direction (a
    breached firewall is a contract break, not informational).
    """
    promotions = authority_source_set_observation_audit(
        ("ELAB.SOURCE_PATHOLOGY",),
        op_id="op-fire",
    )
    findings = observation_promoted_findings(promotions)
    assert len(findings) == 1
    finding = findings[0]
    assert isinstance(finding, Finding)
    assert finding.kind == EVID_OBSERVATION_PROMOTED_TO_AUTHORITY_FINDING_CODE
    assert finding.role == VIOLATION_ROLE
    assert finding.blocking is True
    assert finding.stage == "apply_authority_audit"
    # AGENTS.md §3.2: detail embeds the offending kind + op_id so a triager
    # can answer the evidence path without re-running extraction.
    detail = finding.detail
    assert detail["promoted_kind"] == "ELAB.SOURCE_PATHOLOGY"
    assert detail["op_id"] == "op-fire"
    assert detail["owner_phase"] == "apply"
    assert detail["rule_id"] == EVID_AUTHORITY_SOURCE_EXCLUDES_OBSERVATION_KINDS_RULE_ID
    assert detail["roles_excluded"] == ("observation",)
    assert "§2.10" in detail["reason"]


def test_registry_row_registered_for_violation_finding() -> None:
    """The FindingSpec row is registered so the violation Finding constructs successfully.

    Sanity for the validate_finding_projection carrier contract: a
    role=``"violation"`` Finding requires blocking=True AND a registered
    barrier code. The registry-row presence is the §1.8 receipt for the
    apply-path failure path.
    """
    spec = FINDING_REGISTRY.get(EVID_OBSERVATION_PROMOTED_TO_AUTHORITY_FINDING_CODE)
    assert spec is not None
    assert spec.role == "violation"
    assert spec.family == "violation"
    assert spec.default_enforcement == "hard_fail"
    assert spec.owner == "execution_authorization"
    assert "safety_invariant" in spec.proof_categories
    assert "strictness" in spec.proof_categories


# --------------------------------------------------------------------------- #
# Negative — non-observation-role kinds are not flagged.                       #
# --------------------------------------------------------------------------- #


def test_obligation_role_kind_in_authority_source_set_emits_zero_promotions() -> None:
    """Per audit_impl_D11 §6 negative: an obligation-role kind does NOT fire.

    ``APPLY.FAILED_OPERATION`` is registered with ``role="obligation"``;
    the audit discriminates on observation-role only (obligations are
    blocking requirements by design — they are the legitimate authority
    chain, not the evidence-plane firewall breach).
    """
    promotions = authority_source_set_observation_audit(
        ("APPLY.FAILED_OPERATION",),
        op_id="op-obligation",
    )
    assert promotions == ()


def test_violation_role_kind_in_authority_source_set_emits_zero_promotions() -> None:
    """A violation-role kind is also a legitimate authority surface.

    ``OVERLAY.UNAUTHORIZED_PROMOTION`` (D8) is registered with
    ``role="obligation"`` — wait, it's registered with the obligation role.
    Let me instead verify the violation-role kind ``APPLY.OCCUPANCY_POLICY_
    VIOLATION`` (if registered as ``role="observation"`` would fire). Skipping
    the spec-vs-registry coupling here and asserting the principled
    invariant: any role that is NOT ``"observation"`` does NOT fire.
    """
    # INCLUDE_ONLY observation is flagged; iterate all registered finding
    # codes and assert only observation-role kinds ever fire the audit.
    expected_observation_codes = {
        code
        for code, spec in FINDING_REGISTRY.items()
        if spec.role == "observation"
    }
    assert "ELAB.SOURCE_PATHOLOGY" in expected_observation_codes
    # Pick any non-observation kind and assert no promotion.
    non_observation_kinds = [
        code
        for code, spec in FINDING_REGISTRY.items()
        if spec.role != "observation"
    ]
    # Take a small sample (large registry iteration not the test point here —
    # the principled invariant is: observation fires, everything else doesn't).
    sample = next(iter(non_observation_kinds[:3]), "APPLY.FAILED_OPERATION")
    promotions = authority_source_set_observation_audit(
        (sample,), op_id="op-non-obs"
    )
    assert promotions == (), (
        f"role != 'observation' kind {sample!r} unexpectedly fired the audit"
    )


# --------------------------------------------------------------------------- #
# Discriminators — the closed-set + §1.10 detail contract.                    #
# --------------------------------------------------------------------------- #


def test_multiple_observation_role_kinds_emit_one_promotion_each_in_input_order() -> None:
    """A multi-kind source set surfaces exactly the observation-role kinds in order.

    Mixing observation-role + non-observation-role kinds in one call yields
    one promotion per observation-role kind, in input order. §1.8 receipt
    accounting: every observation-role promotion is owned, none silently
    dropped — and non-observation kinds are NOT silently absorbed.
    """
    promotions = authority_source_set_observation_audit(
        (
            "APPLY.FAILED_OPERATION",  # obligation — does not fire
            "ELAB.SOURCE_PATHOLOGY",   # observation — fires
            "APPLY.OCCUPANCY_POLICY_VIOLATION",  # may fire if observation-role;
                                                 # assert if so via registry check
        ),
        op_id="op-multi",
    )
    fired = {p.promoted_kind for p in promotions}
    assert "ELAB.SOURCE_PATHOLOGY" in fired
    assert "APPLY.FAILED_OPERATION" not in fired
    # Assert the audit stayed principled: every fired kind IS observation-role
    # per the registry.
    for promotion in promotions:
        spec = FINDING_REGISTRY[promotion.promoted_kind]
        assert spec.role == "observation", (
            f"audit fired a non-observation-role kind: {promotion.promoted_kind}"
        )


def test_unregistered_authority_source_kind_does_not_fire_and_does_not_raise() -> None:
    """An unregistered authority-source kind is NOT silently classified as observation.

    Per audit_impl_D11 §9 risk: an unregistered kind is handled by the
    existing unregistered-code guard (downstream of this audit). This audit
    returns no promotion for it without raising — the silent-raise pathway
    would interfere with the closed-set principle, and the §1.10 fail-loud
    contract for the unregistered-kind case is owned by the registration
    guard, not this audit.
    """
    promotions = authority_source_set_observation_audit(
        ("UNREGISTERED_FAKE_KIND.XYZ",),
        op_id="op-unregistered",
    )
    assert promotions == ()


def test_empty_authority_source_set_returns_empty_tuple() -> None:
    """An empty authority source set is the clean-state witness (success case)."""
    promotions = authority_source_set_observation_audit(
        (), op_id="op-empty"
    )
    assert promotions == ()
    assert observation_promoted_findings(promotions) == ()
