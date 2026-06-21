"""Tests for the UK application/modification overlay manual claim (M5).

Covers (per AGENTS.md §15):
  - claim schema + dict round-trip
  - validator accepts a valid owned claim for each recognized overlay kind,
    including the deixis-composed N4 case that REUSES M6's deixis resolution
    (``deictic_applying_provision`` + the N4 ``(as inserted)`` source shape)
  - validator rejects malformed / mismatched / inconsistent claims across all
    three stages (schema, source-binding incl. a plain TEXTUAL-amendment effect,
    scope-consistency), plus a deictic claim over a non-deictic source
  - gate emits the NON-replayable recorded-overlay finding only when validated,
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
from lawvm.uk_legislation.application_overlay_claim import (
    APPLICATION_OVERLAY_CLAIM_KIND,
    APPLICATION_OVERLAY_CLAIM_TEMPLATE_RULE_ID,
    APPLICATION_OVERLAY_FINDING_EMITTED_RULE_ID,
    APPLICATION_OVERLAY_FINDING_WITHHELD_RULE_ID,
    APPLICATION_OVERLAY_PROOF_SEMANTIC,
    CLAIM_REJECTED_SCHEMA_RULE_ID,
    CLAIM_REJECTED_SCOPE_CONSISTENCY_RULE_ID,
    CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID,
    CLAIM_VALIDATED_RULE_ID,
    OVERLAY_KIND_APPLIED,
    OVERLAY_KIND_APPLIED_WITH_MODIFICATIONS,
    OVERLAY_KIND_EXCLUDED,
    OVERLAY_KIND_MODIFIED,
    OVERLAY_KIND_RESTRICTED,
    SCOPE_FOR_PURPOSES,
    SCOPE_IN_RELATION_TO,
    SCOPE_UNCONDITIONAL,
    ApplicationOverlayClaim,
    claim_from_dict,
    gate_application_overlay_claim,
    validate_application_overlay_claim,
)

# A real-shaped non-textual modification effect surface (witness ukpga/2006/46
# s.1297 <- uksi/2007/1093 art.10 "modified", scoped in-relation-to).
_MODIFIED_SOURCE = (
    "Section 1297 is modified in relation to overseas companies by article 10."
)
# The N4 deixis-composed source: an application-by-reference effect whose applying
# provision is identified deictically — REUSED by M6 (deixis_application_claim).
_DEIXIS_SOURCE = "applied by SSI 2005/467 reg. 33(2) (as inserted)"


def _claim(**overrides: Any) -> ApplicationOverlayClaim:
    base = ApplicationOverlayClaim(
        claim_id="claim-1",
        claim_kind=APPLICATION_OVERLAY_CLAIM_KIND,
        statute_id="ukpga/2006/46",
        effect_id="key-modified-1297",
        affected_target="ukpga/2006/46/section:1297",
        overlay_kind=OVERLAY_KIND_MODIFIED,
        scope_kind=SCOPE_IN_RELATION_TO,
        applying_instrument_id="uksi/2007/1093",
        source_snippet=_MODIFIED_SOURCE,
        scope_predicate="in relation to overseas companies",
        applying_provision_ref="art. 10",
        temporal_window="",
        deictic_applying_provision="",
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
            "claim_kind": APPLICATION_OVERLAY_CLAIM_KIND,
            "statute_id": "ukpga/2006/46",
            "effect_id": "e",
            "affected_target": "ukpga/2006/46/section:1297",
            "overlay_kind": OVERLAY_KIND_MODIFIED,
            "scope_kind": SCOPE_UNCONDITIONAL,
            "applying_instrument_id": "uksi/2007/1093",
            "source_snippet": "modified",
        }
    )
    assert claim.scope_predicate == ""
    assert claim.temporal_window == ""
    assert claim.deictic_applying_provision == ""
    assert claim.claim_status == "proposed"


# ── validator: accept each overlay kind ──────────────────────────────────────
def test_validate_accepts_modified_in_relation_to() -> None:
    v = validate_application_overlay_claim(_claim())
    assert v.validated
    assert v.rule_id == CLAIM_VALIDATED_RULE_ID
    assert v.proof_semantic == APPLICATION_OVERLAY_PROOF_SEMANTIC


def test_validate_accepts_excluded_temp_window() -> None:
    claim = _claim(
        overlay_kind=OVERLAY_KIND_EXCLUDED,
        scope_kind=SCOPE_FOR_PURPOSES,
        scope_predicate="for the purposes of Part 16",
        source_snippet="excluded (temp.)",
        temporal_window="temp.",
    )
    v = validate_application_overlay_claim(claim)
    assert v.validated


def test_validate_accepts_restricted_unconditional() -> None:
    claim = _claim(
        overlay_kind=OVERLAY_KIND_RESTRICTED,
        scope_kind=SCOPE_UNCONDITIONAL,
        scope_predicate="",
        source_snippet="restricted",
    )
    v = validate_application_overlay_claim(claim)
    assert v.validated


def test_validate_accepts_applied_unconditional() -> None:
    claim = _claim(
        overlay_kind=OVERLAY_KIND_APPLIED,
        scope_kind=SCOPE_UNCONDITIONAL,
        scope_predicate="",
        source_snippet="applied",
    )
    v = validate_application_overlay_claim(claim)
    assert v.validated


def test_validate_accepts_applied_with_modifications() -> None:
    claim = _claim(
        overlay_kind=OVERLAY_KIND_APPLIED_WITH_MODIFICATIONS,
        scope_kind=SCOPE_UNCONDITIONAL,
        scope_predicate="",
        source_snippet="applied (with modifications)",
    )
    v = validate_application_overlay_claim(claim)
    assert v.validated


def test_validate_accepts_matching_effect() -> None:
    effect = _FakeEffect(effect_id="key-modified-1297", effect_type=_MODIFIED_SOURCE)
    v = validate_application_overlay_claim(_claim(), effect=effect)
    assert v.validated


# ── validator: accept the M6 deixis-composed N4 case ─────────────────────────
def test_validate_accepts_deixis_composed_overlay() -> None:
    # The applying provision is identified deictically; M5 carries the M6-resolved
    # provision and REUSES M6's deixis recognizer (it does not re-resolve).
    claim = _claim(
        overlay_kind=OVERLAY_KIND_APPLIED,
        scope_kind=SCOPE_UNCONDITIONAL,
        scope_predicate="",
        applying_instrument_id="ssi/2005/467",
        applying_provision_ref="reg. 33(2)",
        source_snippet=_DEIXIS_SOURCE,
        deictic_applying_provision="ssi/2005/467/regulation:33/subsection:2",
    )
    v = validate_application_overlay_claim(claim)
    assert v.validated


# ── validator: reject schema ─────────────────────────────────────────────────
def test_validate_rejects_unknown_kind() -> None:
    v = validate_application_overlay_claim(_claim(claim_kind="nonsense"))
    assert v.rule_id == CLAIM_REJECTED_SCHEMA_RULE_ID


def test_validate_rejects_unknown_overlay_kind() -> None:
    v = validate_application_overlay_claim(_claim(overlay_kind="rephrased"))
    assert v.rule_id == CLAIM_REJECTED_SCHEMA_RULE_ID


def test_validate_rejects_unknown_scope_kind() -> None:
    v = validate_application_overlay_claim(_claim(scope_kind="vibes"))
    assert v.rule_id == CLAIM_REJECTED_SCHEMA_RULE_ID


def test_validate_rejects_missing_applying_instrument() -> None:
    v = validate_application_overlay_claim(_claim(applying_instrument_id=""))
    assert v.rule_id == CLAIM_REJECTED_SCHEMA_RULE_ID


def test_validate_rejects_scoped_kind_without_predicate() -> None:
    v = validate_application_overlay_claim(_claim(scope_predicate=""))
    assert v.rule_id == CLAIM_REJECTED_SCHEMA_RULE_ID


# ── validator: reject source binding ─────────────────────────────────────────
def test_validate_rejects_textual_amendment_effect() -> None:
    # A plain textual-amendment effect (substitution) is NOT an overlay; rejected
    # at source-binding so the claim can never re-skin a real text mutation.
    v = validate_application_overlay_claim(
        _claim(source_snippet="For section 1297 substitute— new text.")
    )
    assert v.rule_id == CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID


def test_validate_rejects_free_form_source() -> None:
    v = validate_application_overlay_claim(
        _claim(source_snippet="Section 1297 deals with overseas companies.")
    )
    assert v.rule_id == CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID


def test_validate_rejects_effect_id_mismatch() -> None:
    effect = _FakeEffect(effect_id="other", effect_type=_MODIFIED_SOURCE)
    v = validate_application_overlay_claim(_claim(), effect=effect)
    assert v.rule_id == CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID


def test_validate_rejects_effect_without_overlay_shape() -> None:
    effect = _FakeEffect(effect_id="key-modified-1297", effect_type="words substituted")
    v = validate_application_overlay_claim(_claim(), effect=effect)
    assert v.rule_id == CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID


def test_validate_rejects_deictic_provision_over_non_deictic_source() -> None:
    # Carrying a deictic_applying_provision over a non-deictic source is rejected:
    # M5 may only reuse an M6 resolution for a real N4 deixis effect.
    v = validate_application_overlay_claim(
        _claim(deictic_applying_provision="ssi/2005/467/regulation:33/subsection:2")
    )
    assert v.rule_id == CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID


# ── validator: reject scope consistency ──────────────────────────────────────
def test_validate_rejects_unconditional_with_predicate() -> None:
    v = validate_application_overlay_claim(
        _claim(
            scope_kind=SCOPE_UNCONDITIONAL,
            scope_predicate="in relation to overseas companies",
            source_snippet="restricted",
        )
    )
    assert v.rule_id == CLAIM_REJECTED_SCOPE_CONSISTENCY_RULE_ID


def test_validate_rejects_scope_predicate_kind_mismatch() -> None:
    # scope_kind says for-purposes but the predicate surface is an in-relation-to.
    v = validate_application_overlay_claim(
        _claim(
            scope_kind=SCOPE_FOR_PURPOSES,
            scope_predicate="in relation to overseas companies",
        )
    )
    assert v.rule_id == CLAIM_REJECTED_SCOPE_CONSISTENCY_RULE_ID


def test_validate_rejects_incoherent_temporal_window() -> None:
    v = validate_application_overlay_claim(_claim(temporal_window="overseas companies"))
    assert v.rule_id == CLAIM_REJECTED_SCOPE_CONSISTENCY_RULE_ID


# ── gate: emit / withhold ────────────────────────────────────────────────────
def test_gate_emits_finding_when_validated() -> None:
    g = gate_application_overlay_claim(_claim(), validated=True)
    assert g.emitted
    assert g.rule_id == APPLICATION_OVERLAY_FINDING_EMITTED_RULE_ID
    assert g.finding is not None
    finding = g.finding
    # The finding is a NON-replayable record, not a text op.
    assert finding.replayable is False
    assert finding.overlay_kind == OVERLAY_KIND_MODIFIED
    assert finding.scope_predicate == "in relation to overseas companies"
    row = finding.to_dict()
    assert row["replayable"] is False
    assert row["proof_semantic"] == APPLICATION_OVERLAY_PROOF_SEMANTIC
    assert row["affected_target"] == "ukpga/2006/46/section:1297"


def test_gate_emits_deixis_composed_finding_carrying_m6_resolution() -> None:
    claim = _claim(
        overlay_kind=OVERLAY_KIND_APPLIED,
        scope_kind=SCOPE_UNCONDITIONAL,
        scope_predicate="",
        applying_instrument_id="ssi/2005/467",
        applying_provision_ref="reg. 33(2)",
        source_snippet=_DEIXIS_SOURCE,
        deictic_applying_provision="ssi/2005/467/regulation:33/subsection:2",
    )
    g = gate_application_overlay_claim(claim, validated=True)
    assert g.emitted
    assert g.finding is not None
    # The overlay finding references the M6-resolved provision (not re-resolved).
    assert (
        g.finding.deictic_applying_provision
        == "ssi/2005/467/regulation:33/subsection:2"
    )


def test_gate_withholds_when_not_validated() -> None:
    g = gate_application_overlay_claim(_claim(), validated=False)
    assert not g.emitted
    assert g.rule_id == APPLICATION_OVERLAY_FINDING_WITHHELD_RULE_ID
    assert g.finding is None


# ── registry registration ────────────────────────────────────────────────────
def test_proof_semantic_registered() -> None:
    assert APPLICATION_OVERLAY_PROOF_SEMANTIC in UK_OPERATION_FAMILY_PROOF_SEMANTICS


def test_candidate_rule_id_advertises_claim_template() -> None:
    assert (
        APPLICATION_OVERLAY_CLAIM_TEMPLATE_RULE_ID
        in UK_MANUAL_CLAIM_TEMPLATE_RULE_IDS
    )


def test_validator_and_gate_rule_ids_cataloged() -> None:
    from lawvm.tools.spec_ledger_uk_catalog import _UK_RULE_SPECS

    for rule_id in (
        APPLICATION_OVERLAY_CLAIM_TEMPLATE_RULE_ID,
        CLAIM_VALIDATED_RULE_ID,
        CLAIM_REJECTED_SCHEMA_RULE_ID,
        CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID,
        CLAIM_REJECTED_SCOPE_CONSISTENCY_RULE_ID,
        APPLICATION_OVERLAY_FINDING_EMITTED_RULE_ID,
        APPLICATION_OVERLAY_FINDING_WITHHELD_RULE_ID,
    ):
        assert rule_id in _UK_RULE_SPECS
