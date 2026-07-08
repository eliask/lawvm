"""Tests for the same-moment cross-act precedence CLAIM half (§1.7 resolution).

The M2 finding ``uk_same_moment_cross_act_incompatible_payload_ambiguous`` makes
a same-(effective_date, target) cross-act incompatible-payload collision VISIBLE
and records the order-based pick as ``affecting_act_id_lexical_order_unproven``.
This module's claim is the owned RESOLUTION: which affecting act prevails, on a
recognized basis. These tests cover schema, the staged validator
(schema -> conflict-binding -> basis), the opt-in reorder/finding-flip on a
validated claim, and the no-reorder guarantees on absent/invalid claims.

Real corpus witness: SI 2000/1043 reg. 11(3) is substituted at 2005-07-16 by
BOTH uksi/2005/894 and wsi/2005/1806 (a UK SI and a Welsh SI).
"""

from __future__ import annotations

import pytest

from lawvm.core.cross_act_same_moment import (
    RESOLUTION_RESOLVED_BY_CLAIM,
    same_moment_conflict_finding_kind,
)
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.manual_claim_templates import (
    UK_MANUAL_CLAIM_TEMPLATE_RULE_IDS,
)
from lawvm.uk_legislation.ordering import (
    _order_uk_effects_for_replay,
    conflicts_from_effects,
)
from lawvm.uk_legislation.same_moment_precedence_claim import (
    BASIS_DEVOLUTION_TERRITORIAL_EXTENT_SPLIT,
    BASIS_LATER_ENACTMENT,
    CLAIM_REJECTED_BASIS_RULE_ID,
    CLAIM_REJECTED_CONFLICT_BINDING_RULE_ID,
    CLAIM_REJECTED_SCHEMA_RULE_ID,
    CLAIM_VALIDATED_RULE_ID,
    RESOLUTION_LEXICAL_ORDER_UNPROVEN,
    SAME_MOMENT_PRECEDENCE_CLAIM_KIND,
    SAME_MOMENT_PRECEDENCE_CLAIM_TEMPLATE_RULE_ID,
    SAME_MOMENT_PRECEDENCE_RESOLUTION_PROOF_SEMANTIC,
    SameMomentPrecedenceClaim,
    claim_from_dict,
    validate_same_moment_precedence_claim,
)
from lawvm.tools.uk_semantic_claims import UK_OPERATION_FAMILY_PROOF_SEMANTICS

_CONFLICT_RULE_ID = same_moment_conflict_finding_kind("uk")
_TARGET = "reg. 11(3)"
_DATE = "2005-07-16"


def _effect(
    *,
    effect_id: str,
    affecting_uri: str,
    affecting_class: str,
    affecting_number: str,
    effect_type: str = "substituted",
    effective_date: str = _DATE,
    affected_provisions: str = _TARGET,
) -> UKEffectRecord:
    return UKEffectRecord(
        effect_id=effect_id,
        effect_type=effect_type,
        applied=True,
        requires_applied=False,
        modified="2005-01-01",
        affected_uri="/id/uksi/2000/1043",
        affected_class="UnitedKingdomStatutoryInstrument",
        affected_year="2000",
        affected_number="1043",
        affected_provisions=affected_provisions,
        affecting_uri=affecting_uri,
        affecting_class=affecting_class,
        affecting_year="2005",
        affecting_number=affecting_number,
        affecting_provisions="reg. 1",
        affecting_title="Test Affecting Instrument",
        in_force_dates=[{"date": effective_date, "prospective": "false"}],
    )


def _uk_si() -> UKEffectRecord:
    return _effect(
        effect_id="eUK",
        affecting_uri="/id/uksi/2005/894",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_number="894",
    )


def _welsh_si() -> UKEffectRecord:
    return _effect(
        effect_id="eWSI",
        affecting_uri="/id/wsi/2005/1806",
        affecting_class="WelshStatutoryInstrument",
        affecting_number="1806",
    )


def _conflicting_acts(*effects: UKEffectRecord) -> tuple[str, ...]:
    return tuple(sorted({e.affecting_act_id for e in effects}))


def _conflict_findings(diagnostics: list[dict]) -> list[dict]:
    return [d for d in diagnostics if d.get("rule_id") == _CONFLICT_RULE_ID]


def _valid_claim(winner: str, basis: str = BASIS_DEVOLUTION_TERRITORIAL_EXTENT_SPLIT) -> SameMomentPrecedenceClaim:
    return SameMomentPrecedenceClaim(
        claim_id="c1",
        claim_kind=SAME_MOMENT_PRECEDENCE_CLAIM_KIND,
        statute_id="uksi/2000/1043",
        effective_date=_DATE,
        affected_target=_TARGET,
        conflicting_affecting_acts=_conflicting_acts(_uk_si(), _welsh_si()),
        winner_affecting_act_id=winner,
        basis=basis,
    )


# ── Schema / round-trip ──────────────────────────────────────────────────────
def test_claim_round_trips_through_dict() -> None:
    claim = _valid_claim(_welsh_si().affecting_act_id)
    rebuilt = claim_from_dict(claim.to_dict())
    assert rebuilt == claim


def test_claim_dict_stays_effect_level_not_op_level() -> None:
    claim = _valid_claim(_welsh_si().affecting_act_id)
    row = claim.to_dict()

    assert "winner_effect_id" in row
    assert "winner_op_id" not in row


def test_claim_from_dict_accepts_single_act_string() -> None:
    # A single-string conflicting_affecting_acts is normalized to a tuple; the
    # schema stage then rejects it (needs >=2 acts).
    rebuilt = claim_from_dict(
        {
            "claim_id": "c",
            "claim_kind": SAME_MOMENT_PRECEDENCE_CLAIM_KIND,
            "effective_date": _DATE,
            "affected_target": _TARGET,
            "conflicting_affecting_acts": "uksi/2005/894",
            "winner_affecting_act_id": "uksi/2005/894",
            "basis": BASIS_LATER_ENACTMENT,
        }
    )
    assert rebuilt.conflicting_affecting_acts == ("uksi/2005/894",)


# ── Validator: detection surface ─────────────────────────────────────────────
def test_conflicts_from_effects_surfaces_the_real_conflict() -> None:
    detected = conflicts_from_effects([_uk_si(), _welsh_si()])
    assert len(detected) == 1
    conflict = detected[0]
    assert conflict.effective_date == _DATE
    assert conflict.affected_target == _TARGET
    assert conflict.conflicting_affecting_acts == ("uksi/2005/894", "wsi/2005/1806")
    assert set(conflict.conflicting_effect_ids) == {"eUK", "eWSI"}


# ── Validator: accept ────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "winner",
    ["uksi/2005/894", "wsi/2005/1806"],
)
def test_validator_accepts_either_conflicting_act_as_winner(winner: str) -> None:
    detected = conflicts_from_effects([_uk_si(), _welsh_si()])
    v = validate_same_moment_precedence_claim(
        _valid_claim(winner), detected_conflicts=detected
    )
    assert v.validated
    assert v.rule_id == CLAIM_VALIDATED_RULE_ID
    assert v.proof_semantic == SAME_MOMENT_PRECEDENCE_RESOLUTION_PROOF_SEMANTIC
    assert v.to_dict()["winner_affecting_act_id"] == winner


def test_validator_accepts_winner_effect_id_bound_to_winning_act() -> None:
    detected = conflicts_from_effects([_uk_si(), _welsh_si()])
    claim = SameMomentPrecedenceClaim(
        claim_id="c1",
        claim_kind=SAME_MOMENT_PRECEDENCE_CLAIM_KIND,
        statute_id="uksi/2000/1043",
        effective_date=_DATE,
        affected_target=_TARGET,
        conflicting_affecting_acts=_conflicting_acts(_uk_si(), _welsh_si()),
        winner_affecting_act_id="wsi/2005/1806",
        basis=BASIS_DEVOLUTION_TERRITORIAL_EXTENT_SPLIT,
        winner_effect_id="eWSI",
    )
    v = validate_same_moment_precedence_claim(claim, detected_conflicts=detected)
    assert v.validated, v.reason


# ── Validator: reject (schema) ───────────────────────────────────────────────
def test_validator_rejects_bad_kind() -> None:
    detected = conflicts_from_effects([_uk_si(), _welsh_si()])
    claim = SameMomentPrecedenceClaim(
        claim_id="c", claim_kind="not_a_kind", effective_date=_DATE,
        affected_target=_TARGET,
        conflicting_affecting_acts=_conflicting_acts(_uk_si(), _welsh_si()),
        winner_affecting_act_id="wsi/2005/1806", basis=BASIS_LATER_ENACTMENT,
    )
    v = validate_same_moment_precedence_claim(claim, detected_conflicts=detected)
    assert not v.validated
    assert v.rule_id == CLAIM_REJECTED_SCHEMA_RULE_ID


def test_validator_rejects_single_act() -> None:
    detected = conflicts_from_effects([_uk_si(), _welsh_si()])
    claim = SameMomentPrecedenceClaim(
        claim_id="c", claim_kind=SAME_MOMENT_PRECEDENCE_CLAIM_KIND,
        effective_date=_DATE, affected_target=_TARGET,
        conflicting_affecting_acts=("uksi/2005/894",),
        winner_affecting_act_id="uksi/2005/894", basis=BASIS_LATER_ENACTMENT,
    )
    v = validate_same_moment_precedence_claim(claim, detected_conflicts=detected)
    assert not v.validated
    assert v.rule_id == CLAIM_REJECTED_SCHEMA_RULE_ID


def test_validator_rejects_non_iso_date() -> None:
    detected = conflicts_from_effects([_uk_si(), _welsh_si()])
    claim = SameMomentPrecedenceClaim(
        claim_id="c", claim_kind=SAME_MOMENT_PRECEDENCE_CLAIM_KIND,
        effective_date="2005", affected_target=_TARGET,
        conflicting_affecting_acts=_conflicting_acts(_uk_si(), _welsh_si()),
        winner_affecting_act_id="wsi/2005/1806", basis=BASIS_LATER_ENACTMENT,
    )
    v = validate_same_moment_precedence_claim(claim, detected_conflicts=detected)
    assert not v.validated
    assert v.rule_id == CLAIM_REJECTED_SCHEMA_RULE_ID


# ── Validator: reject (conflict binding) ─────────────────────────────────────
def test_validator_rejects_when_no_detected_conflict() -> None:
    # Free-form claim with no real conflict to bind to.
    v = validate_same_moment_precedence_claim(
        _valid_claim("wsi/2005/1806"), detected_conflicts=[]
    )
    assert not v.validated
    assert v.rule_id == CLAIM_REJECTED_CONFLICT_BINDING_RULE_ID


def test_validator_rejects_act_set_mismatch() -> None:
    detected = conflicts_from_effects([_uk_si(), _welsh_si()])
    claim = SameMomentPrecedenceClaim(
        claim_id="c", claim_kind=SAME_MOMENT_PRECEDENCE_CLAIM_KIND,
        effective_date=_DATE, affected_target=_TARGET,
        conflicting_affecting_acts=("uksi/2005/894", "uksi/2005/999"),
        winner_affecting_act_id="uksi/2005/894", basis=BASIS_LATER_ENACTMENT,
    )
    v = validate_same_moment_precedence_claim(claim, detected_conflicts=detected)
    assert not v.validated
    assert v.rule_id == CLAIM_REJECTED_CONFLICT_BINDING_RULE_ID


def test_validator_rejects_wrong_target() -> None:
    detected = conflicts_from_effects([_uk_si(), _welsh_si()])
    claim = SameMomentPrecedenceClaim(
        claim_id="c", claim_kind=SAME_MOMENT_PRECEDENCE_CLAIM_KIND,
        effective_date=_DATE, affected_target="reg. 99",
        conflicting_affecting_acts=_conflicting_acts(_uk_si(), _welsh_si()),
        winner_affecting_act_id="wsi/2005/1806", basis=BASIS_LATER_ENACTMENT,
    )
    v = validate_same_moment_precedence_claim(claim, detected_conflicts=detected)
    assert not v.validated
    assert v.rule_id == CLAIM_REJECTED_CONFLICT_BINDING_RULE_ID


def test_validator_rejects_winner_effect_outside_conflict() -> None:
    detected = conflicts_from_effects([_uk_si(), _welsh_si()])
    claim = SameMomentPrecedenceClaim(
        claim_id="c", claim_kind=SAME_MOMENT_PRECEDENCE_CLAIM_KIND,
        effective_date=_DATE, affected_target=_TARGET,
        conflicting_affecting_acts=_conflicting_acts(_uk_si(), _welsh_si()),
        winner_affecting_act_id="wsi/2005/1806", basis=BASIS_LATER_ENACTMENT,
        winner_effect_id="eGHOST",
    )
    v = validate_same_moment_precedence_claim(claim, detected_conflicts=detected)
    assert not v.validated
    assert v.rule_id == CLAIM_REJECTED_CONFLICT_BINDING_RULE_ID


def test_validator_rejects_winner_effect_of_losing_act() -> None:
    detected = conflicts_from_effects([_uk_si(), _welsh_si()])
    claim = SameMomentPrecedenceClaim(
        claim_id="c", claim_kind=SAME_MOMENT_PRECEDENCE_CLAIM_KIND,
        effective_date=_DATE, affected_target=_TARGET,
        conflicting_affecting_acts=_conflicting_acts(_uk_si(), _welsh_si()),
        winner_affecting_act_id="wsi/2005/1806", basis=BASIS_LATER_ENACTMENT,
        winner_effect_id="eUK",  # belongs to the UK SI, not the claimed winner
    )
    v = validate_same_moment_precedence_claim(claim, detected_conflicts=detected)
    assert not v.validated
    assert v.rule_id == CLAIM_REJECTED_CONFLICT_BINDING_RULE_ID


# ── Validator: reject (basis) ────────────────────────────────────────────────
def test_validator_rejects_winner_not_in_conflict() -> None:
    detected = conflicts_from_effects([_uk_si(), _welsh_si()])
    claim = SameMomentPrecedenceClaim(
        claim_id="c", claim_kind=SAME_MOMENT_PRECEDENCE_CLAIM_KIND,
        effective_date=_DATE, affected_target=_TARGET,
        conflicting_affecting_acts=_conflicting_acts(_uk_si(), _welsh_si()),
        winner_affecting_act_id="ukpga/2099/1", basis=BASIS_LATER_ENACTMENT,
    )
    v = validate_same_moment_precedence_claim(claim, detected_conflicts=detected)
    assert not v.validated
    assert v.rule_id == CLAIM_REJECTED_BASIS_RULE_ID


def test_validator_rejects_unrecognized_basis() -> None:
    detected = conflicts_from_effects([_uk_si(), _welsh_si()])
    claim = SameMomentPrecedenceClaim(
        claim_id="c", claim_kind=SAME_MOMENT_PRECEDENCE_CLAIM_KIND,
        effective_date=_DATE, affected_target=_TARGET,
        conflicting_affecting_acts=_conflicting_acts(_uk_si(), _welsh_si()),
        winner_affecting_act_id="wsi/2005/1806", basis="vibes",
    )
    v = validate_same_moment_precedence_claim(claim, detected_conflicts=detected)
    assert not v.validated
    assert v.rule_id == CLAIM_REJECTED_BASIS_RULE_ID


# ── Ordering: default (no claim) is byte-identical + unproven finding ─────────
def test_absent_claim_leaves_default_lexical_order_and_unproven_finding() -> None:
    diagnostics: list[dict] = []
    ordered = _order_uk_effects_for_replay(
        [_uk_si(), _welsh_si()],
        diagnostics_out=diagnostics,
        lowering_observations_out=[],
    )
    # Lexical default: uksi/2005/894 < wsi/2005/1806.
    assert [e.effect_id for e in ordered] == ["eUK", "eWSI"]
    finding = _conflict_findings(diagnostics)[0]
    assert finding["resolution"] == RESOLUTION_LEXICAL_ORDER_UNPROVEN
    assert finding["order_based_winner_affecting_act_id"] == "uksi/2005/894"
    assert finding["blocking"] is True
    assert finding["strict_disposition"] == "block"
    assert "resolved_by_claim_winner_affecting_act_id" not in finding


def test_passing_claims_none_is_byte_identical_to_omitting_it() -> None:
    diag_a: list[dict] = []
    ordered_a = _order_uk_effects_for_replay(
        [_uk_si(), _welsh_si()], diagnostics_out=diag_a, lowering_observations_out=[]
    )
    diag_b: list[dict] = []
    ordered_b = _order_uk_effects_for_replay(
        [_uk_si(), _welsh_si()],
        diagnostics_out=diag_b,
        lowering_observations_out=[],
        same_moment_precedence_claims=None,
    )
    assert [e.effect_id for e in ordered_a] == [e.effect_id for e in ordered_b]
    assert diag_a == diag_b


# ── Ordering: a validated claim reorders + flips the finding ─────────────────
def test_validated_claim_reorders_to_winner_and_flips_finding() -> None:
    # Claim that the Welsh SI prevails (devolution / territorial-extent split).
    claim = _valid_claim("wsi/2005/1806")
    diagnostics: list[dict] = []
    observations: list[dict] = []
    ordered = _order_uk_effects_for_replay(
        [_uk_si(), _welsh_si()],
        diagnostics_out=diagnostics,
        lowering_observations_out=observations,
        same_moment_precedence_claims=[claim],
    )
    # The claimed winner's effect is ordered first, overriding lexical order.
    assert [e.effect_id for e in ordered] == ["eWSI", "eUK"]
    finding = _conflict_findings(diagnostics)[0]
    assert finding["resolution"] == RESOLUTION_RESOLVED_BY_CLAIM
    assert finding["resolved_by_claim_winner_affecting_act_id"] == "wsi/2005/1806"
    assert finding["order_based_winner_affecting_act_id"] == "wsi/2005/1806"
    # Resolved conflict is no longer a blocking ambiguity.
    assert finding["blocking"] is False
    assert any(o.get("rule_id") == _CONFLICT_RULE_ID for o in observations)


def test_validated_claim_for_lexical_winner_keeps_order_but_flips_finding() -> None:
    # Claiming the UK SI (the lexical default) still records resolved_by_claim.
    claim = _valid_claim("uksi/2005/894", basis=BASIS_LATER_ENACTMENT)
    diagnostics: list[dict] = []
    ordered = _order_uk_effects_for_replay(
        [_uk_si(), _welsh_si()],
        diagnostics_out=diagnostics,
        lowering_observations_out=[],
        same_moment_precedence_claims=[claim],
    )
    assert [e.effect_id for e in ordered] == ["eUK", "eWSI"]
    finding = _conflict_findings(diagnostics)[0]
    assert finding["resolution"] == RESOLUTION_RESOLVED_BY_CLAIM
    assert finding["resolved_by_claim_winner_affecting_act_id"] == "uksi/2005/894"


# ── Ordering: an invalid claim never reorders ────────────────────────────────
def test_invalid_claim_does_not_reorder_and_keeps_unproven_finding() -> None:
    # A free-form claim naming an act outside the conflict is rejected and must
    # never reorder; the unproven finding stands.
    bad_claim = SameMomentPrecedenceClaim(
        claim_id="c", claim_kind=SAME_MOMENT_PRECEDENCE_CLAIM_KIND,
        effective_date=_DATE, affected_target=_TARGET,
        conflicting_affecting_acts=_conflicting_acts(_uk_si(), _welsh_si()),
        winner_affecting_act_id="ukpga/2099/1", basis=BASIS_LATER_ENACTMENT,
    )
    diagnostics: list[dict] = []
    ordered = _order_uk_effects_for_replay(
        [_uk_si(), _welsh_si()],
        diagnostics_out=diagnostics,
        lowering_observations_out=[],
        same_moment_precedence_claims=[bad_claim],
    )
    assert [e.effect_id for e in ordered] == ["eUK", "eWSI"]
    finding = _conflict_findings(diagnostics)[0]
    assert finding["resolution"] == RESOLUTION_LEXICAL_ORDER_UNPROVEN


def test_claim_for_other_target_does_not_reorder_this_conflict() -> None:
    claim = SameMomentPrecedenceClaim(
        claim_id="c", claim_kind=SAME_MOMENT_PRECEDENCE_CLAIM_KIND,
        effective_date=_DATE, affected_target="reg. 99",
        conflicting_affecting_acts=_conflicting_acts(_uk_si(), _welsh_si()),
        winner_affecting_act_id="wsi/2005/1806", basis=BASIS_LATER_ENACTMENT,
    )
    diagnostics: list[dict] = []
    ordered = _order_uk_effects_for_replay(
        [_uk_si(), _welsh_si()],
        diagnostics_out=diagnostics,
        lowering_observations_out=[],
        same_moment_precedence_claims=[claim],
    )
    assert [e.effect_id for e in ordered] == ["eUK", "eWSI"]
    finding = _conflict_findings(diagnostics)[0]
    assert finding["resolution"] == RESOLUTION_LEXICAL_ORDER_UNPROVEN


# ── Registry registration ────────────────────────────────────────────────────
def test_template_rule_id_is_registered() -> None:
    assert (
        SAME_MOMENT_PRECEDENCE_CLAIM_TEMPLATE_RULE_ID
        in UK_MANUAL_CLAIM_TEMPLATE_RULE_IDS
    )


def test_proof_semantic_is_registered() -> None:
    assert (
        SAME_MOMENT_PRECEDENCE_RESOLUTION_PROOF_SEMANTIC
        in UK_OPERATION_FAMILY_PROOF_SEMANTICS
    )
