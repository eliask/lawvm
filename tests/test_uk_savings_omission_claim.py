"""Tests for the UK savings-scoped text-omission manual claim.

Covers (per AGENTS.md §15):
  - claim schema + dict round-trip
  - validator accepts a valid owned claim (each recognized saving basis)
  - validator rejects malformed / mismatched / unscoped claims across all three
    stages (schema, source-binding, scope-consistency)
  - gate emits the NON-replayable preserved-scope finding only when validated,
    and withholds (no finding, no text op) when unvalidated
  - registry registration (proof semantic + candidate template + cataloged rules)
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from lawvm.tools.uk_semantic_claims import UK_OPERATION_FAMILY_PROOF_SEMANTICS
from lawvm.uk_legislation.manual_claim_templates import (
    UK_MANUAL_CLAIM_TEMPLATE_RULE_IDS,
)
from lawvm.uk_legislation.savings_omission_claim import (
    BASIS_CATEGORY,
    BASIS_CROSS_REFERENCE,
    BASIS_TEMPORAL_WINDOW,
    CLAIM_REJECTED_SCHEMA_RULE_ID,
    CLAIM_REJECTED_SCOPE_RULE_ID,
    CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID,
    CLAIM_VALIDATED_RULE_ID,
    SAVINGS_SCOPED_OMISSION_CLAIM_KIND,
    SAVINGS_SCOPED_OMISSION_CLAIM_TEMPLATE_RULE_ID,
    SAVINGS_SCOPED_OMISSION_FINDING_EMITTED_RULE_ID,
    SAVINGS_SCOPED_OMISSION_FINDING_WITHHELD_RULE_ID,
    SAVINGS_SCOPED_OMISSION_PROOF_SEMANTIC,
    SavingsScopedOmissionClaim,
    claim_from_dict,
    gate_savings_scoped_omission_claim,
    validate_savings_scoped_omission_claim,
)

# A real-shaped savings-qualified text-omission effect surface: the classifier
# requires "omit ... except in the case of ..." plus a savings window marker.
_SAVING_SNIPPET = (
    "except in the case of a person who immediately before the commencement of "
    "this section held office as registrar"
)
_SOURCE = (
    'In section 5(2) omit the words "and the registrar", ' + _SAVING_SNIPPET + "."
)


def _claim(**overrides: Any) -> SavingsScopedOmissionClaim:
    base = SavingsScopedOmissionClaim(
        claim_id="claim-1",
        claim_kind=SAVINGS_SCOPED_OMISSION_CLAIM_KIND,
        statute_id="ukpga/1999/22",
        effect_id="key-14e2626aff8e6ff508708b7dd0325672",
        affected_target="ukpga/1999/22 s. 5(2)",
        omitted_text='and the registrar',
        omission_anchor="ukpga/1999/22 s. 5(2)",
        saving_basis=BASIS_CATEGORY,
        saving_scope="a person who immediately before the commencement of this section held office as registrar",
        saving_snippet=_SAVING_SNIPPET,
        source_snippet=_SOURCE,
        claimant="reviewer",
        claim_status="proposed",
    )
    return replace(base, **overrides)


@dataclass
class _FakeEffect:
    effect_id: str = ""
    effect_type: str = ""
    comments: str = ""
    source_text: str = ""
    raw_text: str = ""


# ── schema / round-trip ──────────────────────────────────────────────────────
def test_claim_dict_round_trip() -> None:
    claim = _claim()
    assert claim_from_dict(claim.to_dict()) == claim


def test_claim_from_dict_defaults() -> None:
    claim = claim_from_dict(
        {
            "claim_id": "c",
            "claim_kind": SAVINGS_SCOPED_OMISSION_CLAIM_KIND,
            "statute_id": "s",
            "effect_id": "e",
            "affected_target": "t",
            "omitted_text": "x",
            "omission_anchor": "a",
            "saving_basis": BASIS_CATEGORY,
            "saving_scope": "scope",
            "saving_snippet": "except scope",
            "source_snippet": _SOURCE,
        }
    )
    assert claim.claimant == ""
    assert claim.claim_status == "proposed"


# ── validator: accept ────────────────────────────────────────────────────────
def test_validate_accepts_category_basis_claim() -> None:
    v = validate_savings_scoped_omission_claim(_claim())
    assert v.validated
    assert v.rule_id == CLAIM_VALIDATED_RULE_ID
    assert v.proof_semantic == SAVINGS_SCOPED_OMISSION_PROOF_SEMANTIC


def test_validate_accepts_temporal_window_basis_claim() -> None:
    saving = "except in the case of proceedings begun before the commencement of this section"
    source = "Omit subsection (3), " + saving + "."
    claim = _claim(
        saving_basis=BASIS_TEMPORAL_WINDOW,
        saving_scope="proceedings begun before the commencement of this section",
        saving_snippet=saving,
        source_snippet=source,
        omitted_text="subsection (3)",
    )
    v = validate_savings_scoped_omission_claim(claim)
    assert v.validated


def test_validate_accepts_cross_reference_basis_claim() -> None:
    saving = (
        "except in the case of matters preserved by paragraph 3 of Schedule 2, "
        "in the case of which the commencement of this section does not apply"
    )
    source = 'Omit the words "and the deputy", ' + saving + "."
    claim = _claim(
        saving_basis=BASIS_CROSS_REFERENCE,
        saving_scope="matters preserved by paragraph 3 of Schedule 2",
        saving_snippet=saving,
        source_snippet=source,
        omitted_text="and the deputy",
    )
    v = validate_savings_scoped_omission_claim(claim)
    assert v.validated


def test_validate_accepts_matching_effect() -> None:
    effect = _FakeEffect(
        effect_id="key-14e2626aff8e6ff508708b7dd0325672", source_text=_SOURCE
    )
    v = validate_savings_scoped_omission_claim(_claim(), effect=effect)
    assert v.validated


# ── validator: reject schema ─────────────────────────────────────────────────
def test_validate_rejects_unknown_kind() -> None:
    v = validate_savings_scoped_omission_claim(_claim(claim_kind="nonsense"))
    assert v.rule_id == CLAIM_REJECTED_SCHEMA_RULE_ID


def test_validate_rejects_missing_omitted_text() -> None:
    v = validate_savings_scoped_omission_claim(_claim(omitted_text=""))
    assert v.rule_id == CLAIM_REJECTED_SCHEMA_RULE_ID


def test_validate_rejects_missing_anchor() -> None:
    v = validate_savings_scoped_omission_claim(_claim(omission_anchor=""))
    assert v.rule_id == CLAIM_REJECTED_SCHEMA_RULE_ID


def test_validate_rejects_unknown_basis() -> None:
    v = validate_savings_scoped_omission_claim(_claim(saving_basis="guesswork"))
    assert v.rule_id == CLAIM_REJECTED_SCHEMA_RULE_ID


def test_validate_rejects_missing_saving_snippet() -> None:
    v = validate_savings_scoped_omission_claim(_claim(saving_snippet=""))
    assert v.rule_id == CLAIM_REJECTED_SCHEMA_RULE_ID


# ── validator: reject source binding ─────────────────────────────────────────
def test_validate_rejects_unconditional_omission_source() -> None:
    # A plain omission without a savings exception => not the family.
    v = validate_savings_scoped_omission_claim(
        _claim(source_snippet='In section 5(2) omit the words "and the registrar".')
    )
    assert v.rule_id == CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID


def test_validate_rejects_free_form_source() -> None:
    v = validate_savings_scoped_omission_claim(
        _claim(source_snippet="Section 5 is repealed.")
    )
    assert v.rule_id == CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID


def test_validate_rejects_effect_id_mismatch() -> None:
    effect = _FakeEffect(effect_id="other", source_text=_SOURCE)
    v = validate_savings_scoped_omission_claim(_claim(), effect=effect)
    assert v.rule_id == CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID


def test_validate_rejects_effect_without_savings_shape() -> None:
    effect = _FakeEffect(
        effect_id="key-14e2626aff8e6ff508708b7dd0325672",
        source_text="words substituted",
    )
    v = validate_savings_scoped_omission_claim(_claim(), effect=effect)
    assert v.rule_id == CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID


# ── validator: reject scope consistency ──────────────────────────────────────
def test_validate_rejects_scope_not_in_saving_snippet() -> None:
    v = validate_savings_scoped_omission_claim(
        _claim(saving_scope="a wholly invented surviving class not in the snippet")
    )
    assert v.rule_id == CLAIM_REJECTED_SCOPE_RULE_ID


def test_validate_rejects_saving_quoting_back_omitted_span() -> None:
    # The saving snippet is contained in the omitted text => no distinct scope.
    v = validate_savings_scoped_omission_claim(
        _claim(omitted_text="and the registrar " + _SAVING_SNIPPET)
    )
    assert v.rule_id == CLAIM_REJECTED_SCOPE_RULE_ID


def test_validate_rejects_cross_reference_without_reference_target() -> None:
    saving = "except in the case of the previously held entitlements before commencement"
    source = 'Omit the words "x", ' + saving + "."
    claim = _claim(
        saving_basis=BASIS_CROSS_REFERENCE,
        saving_scope="the previously held entitlements",
        saving_snippet=saving,
        source_snippet=source,
        omitted_text="x",
    )
    v = validate_savings_scoped_omission_claim(claim)
    assert v.rule_id == CLAIM_REJECTED_SCOPE_RULE_ID


# ── gate: emit / withhold ────────────────────────────────────────────────────
def test_gate_emits_finding_when_validated() -> None:
    g = gate_savings_scoped_omission_claim(_claim(), validated=True)
    assert g.emitted
    assert g.rule_id == SAVINGS_SCOPED_OMISSION_FINDING_EMITTED_RULE_ID
    assert g.finding is not None
    finding = g.finding
    # The finding is a NON-replayable record, not a text op.
    assert finding.replayable is False
    assert finding.affected_target == "ukpga/1999/22 s. 5(2)"
    assert finding.saving_basis == BASIS_CATEGORY
    row = finding.to_dict()
    assert row["replayable"] is False
    assert row["proof_semantic"] == SAVINGS_SCOPED_OMISSION_PROOF_SEMANTIC


def test_gate_withholds_when_not_validated() -> None:
    g = gate_savings_scoped_omission_claim(_claim(), validated=False)
    assert not g.emitted
    assert g.rule_id == SAVINGS_SCOPED_OMISSION_FINDING_WITHHELD_RULE_ID
    assert g.finding is None


# ── registry registration ────────────────────────────────────────────────────
def test_proof_semantic_registered() -> None:
    assert (
        SAVINGS_SCOPED_OMISSION_PROOF_SEMANTIC in UK_OPERATION_FAMILY_PROOF_SEMANTICS
    )


def test_candidate_rule_id_advertises_claim_template() -> None:
    assert (
        SAVINGS_SCOPED_OMISSION_CLAIM_TEMPLATE_RULE_ID
        in UK_MANUAL_CLAIM_TEMPLATE_RULE_IDS
    )


def test_validator_and_gate_rule_ids_cataloged() -> None:
    from lawvm.tools.spec_ledger_uk_catalog import _UK_RULE_SPECS

    for rule_id in (
        SAVINGS_SCOPED_OMISSION_CLAIM_TEMPLATE_RULE_ID,
        CLAIM_VALIDATED_RULE_ID,
        CLAIM_REJECTED_SCHEMA_RULE_ID,
        CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID,
        CLAIM_REJECTED_SCOPE_RULE_ID,
        SAVINGS_SCOPED_OMISSION_FINDING_EMITTED_RULE_ID,
        SAVINGS_SCOPED_OMISSION_FINDING_WITHHELD_RULE_ID,
    ):
        assert rule_id in _UK_RULE_SPECS
