"""Tests for the UK contingent/conditional-temporal-repeal manual claim.

Covers (per AGENTS.md §15):
  - claim schema
  - validator accepts a valid owned claim
  - validator rejects unsupported / mismatched / unwitnessed claims
  - PIT gate applies at post-trigger PIT and withholds at pre-trigger PIT
  - registry registration (rule id + proof semantic)
"""
from __future__ import annotations

from lawvm.tools.uk_semantic_claims import UK_OPERATION_FAMILY_PROOF_SEMANTICS
from lawvm.uk_legislation.contingent_commencement_claim import (
    CLAIM_REJECTED_SCHEMA_RULE_ID,
    CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID,
    CLAIM_REJECTED_WITNESS_RULE_ID,
    CLAIM_VALIDATED_RULE_ID,
    CONTINGENT_COMMENCEMENT_CLAIM_KIND,
    CONTINGENT_COMMENCEMENT_CLAIM_TEMPLATE_RULE_ID,
    CONTINGENT_COMMENCEMENT_RESOLUTION_PROOF_SEMANTIC,
    CONTINGENT_REPEAL_APPLIED_RULE_ID,
    CONTINGENT_REPEAL_WITHHELD_PRE_DEADLINE_RULE_ID,
    CONTINGENT_REPEAL_WITHHELD_TRIGGER_RULE_ID,
    REPEAL_FIRES_ON_DID_NOT_COMMENCE,
    RESOLUTION_COMMENCED,
    RESOLUTION_DID_NOT_COMMENCE,
    ContingentCommencementClaim,
    claim_from_dict,
    gate_contingent_repeal_at_pit,
    validate_contingent_commencement_claim,
)
from lawvm.uk_legislation.manual_claim_templates import (
    UK_MANUAL_CLAIM_TEMPLATE_RULE_IDS,
)

# A real-shaped conditional-temporal-repeal source snippet.
_SNIPPET = (
    "Section 12 is repealed at the end of 2026 if it has not been brought "
    "into force before the end of 2026."
)


def _did_not_commence_claim(**overrides) -> ContingentCommencementClaim:
    base = dict(
        claim_id="claim-1",
        claim_kind=CONTINGENT_COMMENCEMENT_CLAIM_KIND,
        statute_id="ukpga/2020/1",
        effect_id="e-77",
        trigger_id="ukpga/2020/1/section/12",
        deadline_date="2026-12-31",
        source_snippet=_SNIPPET,
        resolution=RESOLUTION_DID_NOT_COMMENCE,
        repeal_fires_on=REPEAL_FIRES_ON_DID_NOT_COMMENCE,
        claimant="reviewer",
        claim_status="proposed",
    )
    base.update(overrides)
    return ContingentCommencementClaim(**base)


def _commenced_claim(**overrides) -> ContingentCommencementClaim:
    base = dict(
        claim_id="claim-2",
        claim_kind=CONTINGENT_COMMENCEMENT_CLAIM_KIND,
        statute_id="ukpga/2020/1",
        effect_id="e-77",
        trigger_id="ukpga/2020/1/section/12",
        deadline_date="2026-12-31",
        source_snippet=_SNIPPET,
        resolution=RESOLUTION_COMMENCED,
        repeal_fires_on=REPEAL_FIRES_ON_DID_NOT_COMMENCE,
        witness_si_id="uksi/2025/100",
        commenced_by_date="2025-06-01",
        claimant="reviewer",
    )
    base.update(overrides)
    return ContingentCommencementClaim(**base)


# ── Registry registration ────────────────────────────────────────────────────
def test_rule_id_registered_in_template_set():
    assert (
        CONTINGENT_COMMENCEMENT_CLAIM_TEMPLATE_RULE_ID
        in UK_MANUAL_CLAIM_TEMPLATE_RULE_IDS
    )


def test_proof_semantic_registered():
    assert (
        CONTINGENT_COMMENCEMENT_RESOLUTION_PROOF_SEMANTIC
        in UK_OPERATION_FAMILY_PROOF_SEMANTICS
    )


# ── Schema / round-trip ──────────────────────────────────────────────────────
def test_claim_round_trips_through_dict():
    claim = _commenced_claim()
    assert claim_from_dict(claim.to_dict()) == claim


# ── Validator: accept ────────────────────────────────────────────────────────
def test_validator_accepts_did_not_commence_claim():
    result = validate_contingent_commencement_claim(_did_not_commence_claim())
    assert result.validated
    assert result.rule_id == CLAIM_VALIDATED_RULE_ID
    assert result.proof_semantic == CONTINGENT_COMMENCEMENT_RESOLUTION_PROOF_SEMANTIC


def test_validator_accepts_commenced_claim_with_witness():
    result = validate_contingent_commencement_claim(_commenced_claim())
    assert result.validated


# ── Validator: reject ────────────────────────────────────────────────────────
def test_validator_rejects_unknown_claim_kind():
    result = validate_contingent_commencement_claim(
        _did_not_commence_claim(claim_kind="free_form_override")
    )
    assert not result.validated
    assert result.rule_id == CLAIM_REJECTED_SCHEMA_RULE_ID


def test_validator_rejects_unknown_resolution():
    result = validate_contingent_commencement_claim(
        _did_not_commence_claim(resolution="maybe")
    )
    assert not result.validated
    assert result.rule_id == CLAIM_REJECTED_SCHEMA_RULE_ID


def test_validator_rejects_non_iso_deadline():
    result = validate_contingent_commencement_claim(
        _did_not_commence_claim(deadline_date="end of 2026")
    )
    assert not result.validated
    assert result.rule_id == CLAIM_REJECTED_SCHEMA_RULE_ID


def test_validator_rejects_free_form_source_snippet():
    # A snippet that is NOT a conditional-temporal-repeal shape may not be
    # used to override an effect.
    result = validate_contingent_commencement_claim(
        _did_not_commence_claim(source_snippet="Section 12 is repealed.")
    )
    assert not result.validated
    assert result.rule_id == CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID


def test_validator_rejects_commenced_without_witness():
    result = validate_contingent_commencement_claim(
        _commenced_claim(witness_si_id="", commenced_by_date="")
    )
    assert not result.validated
    assert result.rule_id == CLAIM_REJECTED_WITNESS_RULE_ID


def test_validator_rejects_commenced_after_deadline():
    result = validate_contingent_commencement_claim(
        _commenced_claim(commenced_by_date="2027-01-01")
    )
    assert not result.validated
    assert result.rule_id == CLAIM_REJECTED_WITNESS_RULE_ID


def test_validator_rejects_did_not_commence_with_spurious_witness():
    result = validate_contingent_commencement_claim(
        _did_not_commence_claim(witness_si_id="uksi/2025/100")
    )
    assert not result.validated
    assert result.rule_id == CLAIM_REJECTED_WITNESS_RULE_ID


# ── Validator: bound-effect source binding ───────────────────────────────────
class _FakeEffect:
    def __init__(self, effect_id: str, effect_type: str = "", source_text: str = ""):
        self.effect_id = effect_id
        self.effect_type = effect_type
        self.source_text = source_text


def test_validator_rejects_effect_id_mismatch():
    effect = _FakeEffect("e-OTHER", source_text=_SNIPPET)
    result = validate_contingent_commencement_claim(
        _did_not_commence_claim(), effect=effect
    )
    assert not result.validated
    assert result.rule_id == CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID


def test_validator_rejects_effect_without_conditional_repeal_shape():
    effect = _FakeEffect("e-77", source_text="Section 12 is repealed.")
    result = validate_contingent_commencement_claim(
        _did_not_commence_claim(), effect=effect
    )
    assert not result.validated
    assert result.rule_id == CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID


def test_validator_accepts_bound_effect_carrying_conditional_repeal_shape():
    effect = _FakeEffect("e-77", source_text=_SNIPPET)
    result = validate_contingent_commencement_claim(
        _did_not_commence_claim(), effect=effect
    )
    assert result.validated


# ── PIT gate ─────────────────────────────────────────────────────────────────
def test_gate_applies_after_deadline_when_repeal_fires():
    # did_not_commence => repeal fires; PIT past deadline => applies.
    gate = gate_contingent_repeal_at_pit(_did_not_commence_claim(), "2027-01-01")
    assert gate.applies
    assert gate.rule_id == CONTINGENT_REPEAL_APPLIED_RULE_ID


def test_gate_withholds_before_deadline():
    gate = gate_contingent_repeal_at_pit(_did_not_commence_claim(), "2026-01-01")
    assert not gate.applies
    assert gate.rule_id == CONTINGENT_REPEAL_WITHHELD_PRE_DEADLINE_RULE_ID


def test_gate_withholds_when_trigger_commenced():
    # commenced => repeal does NOT fire (the "if not brought into force"
    # condition is false), even past the deadline. This is the forbidden
    # over-repeal direction the claim guards against.
    gate = gate_contingent_repeal_at_pit(_commenced_claim(), "2027-01-01")
    assert not gate.applies
    assert gate.rule_id == CONTINGENT_REPEAL_WITHHELD_TRIGGER_RULE_ID


def test_gate_applies_at_exact_deadline():
    gate = gate_contingent_repeal_at_pit(_did_not_commence_claim(), "2026-12-31")
    assert gate.applies
