"""CTSF mis-typing MUTATION CANARY (audit fix #3).

The primary CTSF gate diffs the residual VERDICT (a family-count multiset) and
fails only when a NEW ``replay_bug`` / ``unknown`` family count appears. That is
blind to one anti-slop hole: a GENUINE, materialized replay defect whose
per-frontend classifier MIS-TYPED it into one of the non-failing families never
increments a FAIL family, so it would ride a benign family to a green gate.

This canary proves the secondary fail-closed audit
(:func:`lawvm.core.ctsf_gate.audit_agreement_residual_family_typing`) closes that
hole: it injects a KNOWN replay-bug residual (a residual carrying a canonical
genuine-replay-defect ``rule_id``) mis-typed into EACH of the 11 non-failing
families in turn, and asserts the audit surfaces every one of them — a mis-typed
defect cannot hide under any non-failing family.
"""

from __future__ import annotations

from lawvm.core.agreement_residual import AgreementResidual, AgreementResidualFamily
from lawvm.core.ctsf_gate import (
    FAIL_FAMILIES,
    audit_agreement_residual_family_typing,
    canonical_billable_replay_defect_rule_ids,
)

_ALL_FAMILIES: tuple[str, ...] = AgreementResidualFamily.__args__
_NON_FAILING_FAMILIES: tuple[str, ...] = tuple(
    f for f in _ALL_FAMILIES if f not in FAIL_FAMILIES
)


def _residual(*, family: str, rule_id: str) -> AgreementResidual:
    """A minimal well-formed residual carrying a chosen family + rule id."""
    return AgreementResidual(
        residual_id=f"canary:{family}:{rule_id}",
        jurisdiction="canary",
        agreement_surface="ctsf_family_typing_canary",
        family=family,  # ty: ignore[invalid-argument-type]
        agreement_residual_status="residual",
        owner_phase="audit",
        rule_id=rule_id,
        safe_default="surface_mis_typing_without_repairing",
        forbidden_shortcuts=("mis_typed_replay_bug_as_benign_family",),
    )


def test_non_failing_family_partition_is_the_expected_eleven() -> None:
    # The FAIL families are exactly the two billable-to-replay families; the rest
    # (11 of them) are the non-failing families a mis-typed defect could hide in.
    assert set(FAIL_FAMILIES) == {"replay_bug", "unknown"}
    assert len(_NON_FAILING_FAMILIES) == 11
    assert "replay_bug" not in _NON_FAILING_FAMILIES
    assert "unknown" not in _NON_FAILING_FAMILIES


def test_known_replay_bug_mis_typed_into_each_non_failing_family_is_surfaced() -> None:
    """A KNOWN replay-bug residual mis-typed into ANY non-failing family is caught."""
    billable_ids = canonical_billable_replay_defect_rule_ids()
    assert billable_ids, "canonical billable replay-defect registry is empty"
    bug_rule_id = sorted(billable_ids)[0]

    surfaced_families: set[str] = set()
    for family in _NON_FAILING_FAMILIES:
        mis_typed = _residual(family=family, rule_id=bug_rule_id)
        violations = audit_agreement_residual_family_typing([mis_typed])
        assert violations == (mis_typed,), (
            f"a known replay-bug residual mis-typed as {family!r} was NOT surfaced "
            f"by the secondary audit — it would ride a non-failing family to a "
            f"green gate"
        )
        surfaced_families.add(family)

    # Every one of the 11 non-failing families was exercised and caught.
    assert surfaced_families == set(_NON_FAILING_FAMILIES)


def test_correctly_typed_billable_is_not_surfaced() -> None:
    """A billable residual CORRECTLY typed into a FAIL family is not a mis-typing."""
    bug_rule_id = sorted(canonical_billable_replay_defect_rule_ids())[0]
    for family in FAIL_FAMILIES:
        ok = _residual(family=family, rule_id=bug_rule_id)
        assert audit_agreement_residual_family_typing([ok]) == ()


def test_benign_rule_id_in_benign_family_is_not_surfaced() -> None:
    """The audit is not a blanket flag: a non-billable rule id is never surfaced.

    Proof the audit has BITE and is not just returning everything: a residual whose
    rule id is NOT a canonical replay defect stays clean in any non-failing family.
    """
    for family in _NON_FAILING_FAMILIES:
        benign = _residual(
            family=family,
            rule_id="some_frontend_source_honest_frontier_rule_id",
        )
        assert audit_agreement_residual_family_typing([benign]) == ()


def test_audit_surfaces_mix_of_mis_typed_and_clean_residuals() -> None:
    """Over a mixed batch, the audit surfaces exactly the mis-typed billables."""
    bug_rule_id = sorted(canonical_billable_replay_defect_rule_ids())[0]
    mis_typed = _residual(family="oracle_editorial_pathology", rule_id=bug_rule_id)
    correctly_typed = _residual(family="replay_bug", rule_id=bug_rule_id)
    benign = _residual(family="agreement", rule_id="frontier_rule_id")

    violations = audit_agreement_residual_family_typing(
        [correctly_typed, mis_typed, benign]
    )
    assert violations == (mis_typed,)
