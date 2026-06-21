from __future__ import annotations

from pathlib import Path

import pytest

from lawvm.new_zealand.commencement import (
    NZ_COMMENCEMENT_RECORDED_RULE_ID,
    NZ_COMMENCEMENT_REFUSED_DATE_NOT_DETERMINATE_RULE_ID,
    NZ_COMMENCEMENT_REFUSED_TARGET_NOT_DETERMINATE_RULE_ID,
    NZCommencementRecord,
    NZCommencementRefusal,
    build_archived_work_commencement_surface,
    build_commencement_surface,
)
from lawvm.new_zealand.operation_surface import build_operation_surface
from lawvm.new_zealand.source_tree import parse_nz_source_document


# A small act with three commencement (brought-into-force) history notes:
#   * a determinate one (a live primary-body provision with an ISO date),
#   * one whose date prose does not resolve to an ISO date,
#   * an editorial/text repeal note that is NOT a commencement (must be ignored).
_COMMENCEMENT_XML = b"""\
<act>
  <cover><title>Example Act 1955</title></cover>
  <body>
    <prov id="p1">
      <label>1</label>
      <heading>Short Title and commencement</heading>
      <notes>
        <history-note id="HN-comm-1">
          <amended-provision>Section 1(2)</amended-provision>
          <amending-operation>brought into force</amending-operation>
          <amendment-date>1 January 1955</amendment-date>
          <amending-leg>Example Act Commencement Order 1954 (SR 1954/220)</amending-leg>
          Section 1(2): this Act brought into force, on 1 January 1955, by the Example Act Commencement Order 1954 (SR 1954/220).
        </history-note>
      </notes>
    </prov>
    <prov id="p2">
      <label>2</label>
      <heading>Interpretation</heading>
      <notes>
        <history-note id="HN-comm-2">
          <amended-provision>Section 2</amended-provision>
          <amending-operation>brought into force</amending-operation>
          <amendment-date>at the commencement of the principal Act</amendment-date>
          <amending-leg>Example Act Commencement Order 1954 (SR 1954/220)</amending-leg>
        </history-note>
        <history-note id="HN-repeal">
          <amended-provision>Section 2</amended-provision>
          <amending-operation>repealed</amending-operation>
          <amendment-date>1 March 1960</amendment-date>
          <amending-leg>Some Amendment Act 1960</amending-leg>
        </history-note>
      </notes>
    </prov>
  </body>
</act>
"""


def _commencement_report_from_xml(xml: bytes):
    document = parse_nz_source_document(xml, xml_locator="loc", version_id="v1")
    surface = build_operation_surface(document, work_id="act_test_1955_1")
    return build_commencement_surface(surface, work_id="act_test_1955_1")


def test_commencement_determinate_witness_is_recorded_as_temporal_state_effect() -> None:
    report = _commencement_report_from_xml(_COMMENCEMENT_XML)
    assert [record.row_id for record in report.records]  # at least one recorded
    record = next(r for r in report.records if r.commencement_date_iso == "1955-01-01")
    assert isinstance(record, NZCommencementRecord)
    # The record carries the resolved target address, the determinate ISO date,
    # and the commencing instrument — and it is NOT a text mutation.
    assert record.target_address.startswith("section:1")
    assert record.commencement_date_iso == "1955-01-01"
    assert "Commencement Order 1954" in record.commencing_instrument
    jsonable = record.to_jsonable()
    assert jsonable["effect_kind"] == "commencement_in_force_status"
    assert jsonable["is_text_mutation"] is False
    assert jsonable["rule_id"] == NZ_COMMENCEMENT_RECORDED_RULE_ID


def test_commencement_without_iso_date_is_refused_as_frontier_residue() -> None:
    report = _commencement_report_from_xml(_COMMENCEMENT_XML)
    undated = [r for r in report.refusals if not r.commencement_date_iso]
    assert undated, "the undated commencement note must become typed frontier residue"
    assert all(
        r.rule_id == NZ_COMMENCEMENT_REFUSED_DATE_NOT_DETERMINATE_RULE_ID for r in undated
    )


def test_commencement_surface_ignores_non_commencement_operations() -> None:
    report = _commencement_report_from_xml(_COMMENCEMENT_XML)
    # The repeal note is a TEXT family and must never enter the commencement
    # surface — only brought-into-force witnesses are typed here.
    all_xml_ids = {r.source_xml_id for r in report.records} | {
        r.source_xml_id for r in report.refusals
    }
    assert "HN-repeal" not in all_xml_ids


def test_commencement_refuses_indeterminate_target_address() -> None:
    # A commencement witness whose target hint does not parse to a determinate
    # address candidate (here a free-text scope) must be refused, never recorded
    # against a guessed address.
    xml = b"""\
<act>
  <cover><title>Indeterminate Target Act 1970</title></cover>
  <body>
    <prov id="p1">
      <label>1</label>
      <heading>Commencement</heading>
      <text>Commencement provision.</text>
      <history>
        <history-note id="HN-vague">
          <amended-provision>the whole of this Act except as otherwise provided</amended-provision>
          <amending-operation>brought into force</amending-operation>
          <amendment-date>1 April 1970</amendment-date>
          <amending-leg>Indeterminate Act Commencement Order 1970</amending-leg>
        </history-note>
      </history>
    </prov>
  </body>
</act>
"""
    document = parse_nz_source_document(xml, xml_locator="loc", version_id="v1")
    surface = build_operation_surface(document, work_id="act_test_1970_1")
    report = build_commencement_surface(surface, work_id="act_test_1970_1")
    assert not report.records
    assert report.refusals
    refusal = report.refusals[0]
    assert isinstance(refusal, NZCommencementRefusal)
    assert refusal.rule_id == NZ_COMMENCEMENT_REFUSED_TARGET_NOT_DETERMINATE_RULE_ID
    assert refusal.commencement_date_iso == "1970-04-01"


def test_commencement_agreement_surface_never_claims_text_agreement() -> None:
    report = _commencement_report_from_xml(_COMMENCEMENT_XML)
    residuals = report.agreement_residuals()
    # No row claims a text-slice agreement: a recorded commencement is a typed
    # temporal-state record on a non-text axis, and frontier residue is a typed
    # accepted frontier. Neither is an ``agrees`` text comparison.
    statuses = {residual.agreement_residual_status for residual in residuals}
    assert "agrees" not in statuses
    assert statuses == {"frontier"}
    families = {residual.family for residual in residuals}
    assert "non_commensurable_surface" in families  # the recorded determinate one
    assert "accepted_non_executable_frontier" in families  # the residue
    # The surface dict is well-formed and carries no text-agreement claim.
    surface = report.agreement_surface()
    assert surface["agreement_surface"] == "nz_commencement"
    assert surface["materialization_kind"] == "legal_text_state"
    # The summary makes the no-text-mutation, no-replay claim explicit.
    summary = report.summary()
    assert summary["is_text_mutation_family"] is False
    assert summary["replay_claims"] is False
    assert summary["text_agreement_claims"] is False
    assert summary["recorded"] + summary["frontier_residue"] == summary["commencement_witnesses"]


@pytest.mark.parametrize(
    "work_id",
    [
        "act_public_1953_64",  # "this Act brought into force, on 1 January 1955"
        "act_public_1960_47",  # "sections 4-6 brought into force, on 27 June 1961"
    ],
)
def test_commencement_surface_records_archived_brought_into_force_rows(work_id: str) -> None:
    db_path = Path("data/nz_legislation.farchive")
    if not db_path.exists():
        pytest.skip("nz farchive not available in this environment")
    report = build_archived_work_commencement_surface(db_path, work_id)
    summary = report.summary()
    assert summary["commencement_witnesses"] >= 1
    assert summary["recorded"] >= 1
    # Every recorded archived row carries a determinate ISO date + target and is
    # not a text mutation.
    for record in report.records:
        assert record.commencement_date_iso
        assert record.target_address
        assert record.to_jsonable()["is_text_mutation"] is False
