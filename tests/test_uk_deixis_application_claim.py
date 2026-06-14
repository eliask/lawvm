"""Tests for the UK deixis-in-application manual claim (M6).

Covers (per AGENTS.md §15):
  - claim schema + dict round-trip
  - validator accepts a valid owned claim (each recognized basis)
  - validator rejects malformed / mismatched / unreachable claims across all
    three stages (schema, source-binding, resolution-consistency)
  - gate emits the NON-replayable resolved-reference finding only when validated,
    and withholds (no finding, no text op) when unvalidated
  - registry registration (proof semantic + candidate template + cataloged rules)
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from lawvm.tools.uk_semantic_claims import UK_OPERATION_FAMILY_PROOF_SEMANTICS
from lawvm.uk_legislation.deixis_application_claim import (
    BASIS_COMMENCEMENT_INSERTED_TEXT,
    BASIS_INSERTING_AMENDMENT_PROGRAM,
    CLAIM_REJECTED_RESOLUTION_RULE_ID,
    CLAIM_REJECTED_SCHEMA_RULE_ID,
    CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID,
    CLAIM_VALIDATED_RULE_ID,
    DEIXIS_IN_APPLICATION_CLAIM_KIND,
    DEIXIS_IN_APPLICATION_CLAIM_TEMPLATE_RULE_ID,
    DEIXIS_IN_APPLICATION_FINDING_EMITTED_RULE_ID,
    DEIXIS_IN_APPLICATION_FINDING_WITHHELD_RULE_ID,
    DEIXIS_IN_APPLICATION_RESOLUTION_PROOF_SEMANTIC,
    DeixisInApplicationClaim,
    claim_from_dict,
    gate_deixis_in_application_claim,
    validate_deixis_in_application_claim,
)
from lawvm.uk_legislation.manual_claim_templates import (
    UK_MANUAL_CLAIM_TEMPLATE_RULE_IDS,
)

# A real-shaped N4 application-by-reference-with-deixis effect surface.
_N4_SOURCE = "applied by SSI 2005/467 reg. 33(2) (as inserted)"
# A real-shaped inserting-program (applying-instrument) inserted-anchor snippet,
# which the cat-4 recognizer accepts and which mentions the deictic label.
_INSERTING_SNIPPET = (
    "after paragraph (1) as inserted by S.S.I. 2003/176, insert— (2) the body"
)


def _claim(**overrides: Any) -> DeixisInApplicationClaim:
    base = DeixisInApplicationClaim(
        claim_id="claim-1",
        claim_kind=DEIXIS_IN_APPLICATION_CLAIM_KIND,
        statute_id="asp/2003/13",
        effect_id="key-14e2626aff8e6ff508708b7dd0325672",
        affected_target="asp/2003/13 s. 100",
        applying_instrument_id="ssi/2005/467",
        deictic_provision_ref="reg. 33(2)",
        deictic_surface="(as inserted)",
        source_snippet=_N4_SOURCE,
        resolved_provision_eid="reg. 33(2)",
        resolution_basis=BASIS_INSERTING_AMENDMENT_PROGRAM,
        inserting_instrument_id="ssi/2003/176",
        inserting_amendment_ref="reg. 5(3)",
        inserting_program_snippet=_INSERTING_SNIPPET,
        claimant="reviewer",
        status="proposed",
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
            "claim_kind": DEIXIS_IN_APPLICATION_CLAIM_KIND,
            "statute_id": "s",
            "effect_id": "e",
            "affected_target": "t",
            "applying_instrument_id": "i",
            "deictic_provision_ref": "reg. 1(2)",
            "deictic_surface": "(as inserted)",
            "source_snippet": _N4_SOURCE,
            "resolved_provision_eid": "reg. 1(2)",
            "resolution_basis": BASIS_INSERTING_AMENDMENT_PROGRAM,
        }
    )
    assert claim.inserting_instrument_id == ""
    assert claim.status == "proposed"


# ── validator: accept ────────────────────────────────────────────────────────
def test_validate_accepts_inserting_amendment_program_claim() -> None:
    v = validate_deixis_in_application_claim(_claim())
    assert v.validated
    assert v.rule_id == CLAIM_VALIDATED_RULE_ID
    assert v.proof_semantic == DEIXIS_IN_APPLICATION_RESOLUTION_PROOF_SEMANTIC


def test_validate_accepts_commencement_inserted_text_claim() -> None:
    # The commencement basis does not require an inline inserting-program fragment;
    # label-consistency between the deixis and the resolution still holds.
    claim = _claim(
        resolution_basis=BASIS_COMMENCEMENT_INSERTED_TEXT,
        inserting_instrument_id="ssi/2005/333",
        inserting_program_snippet="",
    )
    v = validate_deixis_in_application_claim(claim)
    assert v.validated


def test_validate_accepts_matching_effect() -> None:
    effect = _FakeEffect(
        effect_id="key-14e2626aff8e6ff508708b7dd0325672", effect_type=_N4_SOURCE
    )
    v = validate_deixis_in_application_claim(_claim(), effect=effect)
    assert v.validated


# ── validator: reject schema ─────────────────────────────────────────────────
def test_validate_rejects_unknown_kind() -> None:
    v = validate_deixis_in_application_claim(_claim(claim_kind="nonsense"))
    assert v.rule_id == CLAIM_REJECTED_SCHEMA_RULE_ID


def test_validate_rejects_missing_applying_instrument() -> None:
    v = validate_deixis_in_application_claim(_claim(applying_instrument_id=""))
    assert v.rule_id == CLAIM_REJECTED_SCHEMA_RULE_ID


def test_validate_rejects_non_deictic_surface() -> None:
    v = validate_deixis_in_application_claim(_claim(deictic_surface="(as amended)"))
    assert v.rule_id == CLAIM_REJECTED_SCHEMA_RULE_ID


def test_validate_rejects_unknown_basis() -> None:
    v = validate_deixis_in_application_claim(_claim(resolution_basis="guesswork"))
    assert v.rule_id == CLAIM_REJECTED_SCHEMA_RULE_ID


def test_validate_rejects_inserting_basis_without_program_snippet() -> None:
    v = validate_deixis_in_application_claim(_claim(inserting_program_snippet=""))
    assert v.rule_id == CLAIM_REJECTED_SCHEMA_RULE_ID


# ── validator: reject source binding ─────────────────────────────────────────
def test_validate_rejects_non_n4_source() -> None:
    # Plain application without a deixis => not the N4 family.
    v = validate_deixis_in_application_claim(
        _claim(source_snippet="applied by SSI 2005/467 reg. 33(2)")
    )
    assert v.rule_id == CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID


def test_validate_rejects_free_form_source() -> None:
    v = validate_deixis_in_application_claim(
        _claim(source_snippet="Section 100 is repealed.")
    )
    assert v.rule_id == CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID


def test_validate_rejects_effect_id_mismatch() -> None:
    effect = _FakeEffect(effect_id="other", effect_type=_N4_SOURCE)
    v = validate_deixis_in_application_claim(_claim(), effect=effect)
    assert v.rule_id == CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID


def test_validate_rejects_effect_without_n4_shape() -> None:
    effect = _FakeEffect(
        effect_id="key-14e2626aff8e6ff508708b7dd0325672",
        effect_type="words substituted",
    )
    v = validate_deixis_in_application_claim(_claim(), effect=effect)
    assert v.rule_id == CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID


# ── validator: reject resolution consistency ─────────────────────────────────
def test_validate_rejects_unrecognized_inserting_program() -> None:
    # The cited inserting snippet is not a recognized inserted-anchor program.
    v = validate_deixis_in_application_claim(
        _claim(inserting_program_snippet="something happened, who knows")
    )
    assert v.rule_id == CLAIM_REJECTED_RESOLUTION_RULE_ID


def test_validate_rejects_resolution_label_mismatch() -> None:
    # The resolved provision label does not match the deictic provision label.
    v = validate_deixis_in_application_claim(
        _claim(resolved_provision_eid="reg. 99(9)")
    )
    assert v.rule_id == CLAIM_REJECTED_RESOLUTION_RULE_ID


def test_validate_rejects_inserting_surface_without_deictic_label() -> None:
    # A recognized inserted-anchor program that inserts a DIFFERENT label than the
    # deictic provision => the program does not insert what the deixis denotes.
    v = validate_deixis_in_application_claim(
        _claim(
            deictic_provision_ref="reg. 7(4)",
            resolved_provision_eid="reg. 7(4)",
        )
    )
    assert v.rule_id == CLAIM_REJECTED_RESOLUTION_RULE_ID


# ── gate: emit / withhold ────────────────────────────────────────────────────
def test_gate_emits_finding_when_validated() -> None:
    g = gate_deixis_in_application_claim(_claim(), validated=True)
    assert g.emitted
    assert g.rule_id == DEIXIS_IN_APPLICATION_FINDING_EMITTED_RULE_ID
    assert g.finding is not None
    finding = g.finding
    # The finding is a NON-replayable record, not a text op.
    assert finding.replayable is False
    assert finding.resolved_provision_eid == "reg. 33(2)"
    assert finding.applying_instrument_id == "ssi/2005/467"
    row = finding.to_dict()
    assert row["replayable"] is False
    assert row["proof_semantic"] == DEIXIS_IN_APPLICATION_RESOLUTION_PROOF_SEMANTIC


def test_gate_withholds_when_not_validated() -> None:
    g = gate_deixis_in_application_claim(_claim(), validated=False)
    assert not g.emitted
    assert g.rule_id == DEIXIS_IN_APPLICATION_FINDING_WITHHELD_RULE_ID
    assert g.finding is None


# ── registry registration ────────────────────────────────────────────────────
def test_proof_semantic_registered() -> None:
    assert (
        DEIXIS_IN_APPLICATION_RESOLUTION_PROOF_SEMANTIC
        in UK_OPERATION_FAMILY_PROOF_SEMANTICS
    )


def test_candidate_rule_id_advertises_claim_template() -> None:
    assert (
        DEIXIS_IN_APPLICATION_CLAIM_TEMPLATE_RULE_ID in UK_MANUAL_CLAIM_TEMPLATE_RULE_IDS
    )


def test_validator_and_gate_rule_ids_cataloged() -> None:
    from lawvm.tools.spec_ledger_uk_catalog import _UK_RULE_SPECS

    for rule_id in (
        DEIXIS_IN_APPLICATION_CLAIM_TEMPLATE_RULE_ID,
        CLAIM_VALIDATED_RULE_ID,
        CLAIM_REJECTED_SCHEMA_RULE_ID,
        CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID,
        CLAIM_REJECTED_RESOLUTION_RULE_ID,
        DEIXIS_IN_APPLICATION_FINDING_EMITTED_RULE_ID,
        DEIXIS_IN_APPLICATION_FINDING_WITHHELD_RULE_ID,
    ):
        assert rule_id in _UK_RULE_SPECS
