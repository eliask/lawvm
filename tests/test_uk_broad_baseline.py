from __future__ import annotations

import json
import sys
import time
from types import SimpleNamespace

from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.core.mutation_events import MutationEvent
from lawvm.core.semantic_types import FacetKind, StructuralAction
from lawvm.uk_legislation.frontier_work_items import (
    uk_frontier_work_item_from_manual_frontier_row,
)

import scripts.uk_broad_baseline as uk_broad_baseline


def _uk_available_statute_xml() -> bytes:
    return b"""<?xml version="1.0"?>
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation"
    NumberOfProvisions="1">
  <Body><P1 id="section-1"><Pnumber>1</Pnumber><P1para>Text.</P1para></P1></Body>
</Legislation>"""


def _uk_metadata_only_statute_xml() -> bytes:
    return b"""<?xml version="1.0"?>
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation"
    NumberOfProvisions="0">
  <Metadata><Title>Metadata-only test fixture</Title></Metadata>
</Legislation>"""


def _install_score_one_archive(
    monkeypatch,
    *,
    enacted_xml: bytes,
    current_xml: bytes,
) -> None:
    class FakeFarchive:
        def __init__(self, _path, **_kwargs):
            pass

        def get(self, locator: str) -> bytes | None:
            if locator.endswith("/enacted/data.xml"):
                return enacted_xml
            if locator.endswith("/data.xml"):
                return current_xml
            return None

        def close(self) -> None:
            pass

    monkeypatch.setitem(
        sys.modules,
        "farchive",
        SimpleNamespace(Farchive=FakeFarchive),
    )


def test_compile_diagnostic_frontier_work_item_uses_family_defaults() -> None:
    work_item = uk_frontier_work_item_from_manual_frontier_row(
        {
            "statute_id": "ukpga/1968/73",
            "effect_id": "eff-table",
            "affecting_act_id": "uksi/2003/1615",
            "affected_provisions": "Sch. 12 table",
            "manual_compile_status": "manual_compile_candidate",
            "manual_compile_rule_id": "uk_manual_frontier_table_entry_candidate",
            "owner_phase": "typed_elaboration",
            "replay_authorized": False,
            "executable": False,
            "authorization_status": "manual_claim_required",
            "required_proofs": ["mutation_boundary_proof"],
            "safe_default": "block_until_validated_claim_authorizes_replay",
            "forbidden_shortcuts": ["unvalidated_manual_claim_execution"],
        }
    ).to_dict()

    assert work_item["candidate_operation_family"] == "table_surface_mutation"
    assert work_item["detail"]["target_resolution_coverage"][
        "target_resolution_status"
    ] == "resolved"
    assert work_item["detail"]["target_resolution_coverage"]["source_target"] == (
        "Sch. 12 table"
    )
    assert work_item["detail"]["packet_completeness"][
        "has_target_resolution_coverage"
    ] is True
    assert work_item["required_validator_checks"] == [
        "source_witness_targets_table_entry_or_column_surface",
        "claim_identifies_exact_table_carrier",
        "claim_identifies_row_or_column_boundary",
        "claim_preserves_unclaimed_rows_columns_and_cells",
        "changed_paths_are_within_claimed_table_surface",
    ]


def test_score_one_reports_multiple_choices_current_as_source_frontier(monkeypatch) -> None:
    class FakeFarchive:
        def __init__(self, _path, **_kwargs):
            pass

        def get(self, locator: str) -> bytes | None:
            if locator.endswith("/enacted/data.xml"):
                return b"""<?xml version="1.0"?>
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation"
    NumberOfProvisions="1">
  <Body><P1 id="section-1"><Pnumber>1</Pnumber><P1para>Text.</P1para></P1></Body>
</Legislation>"""
            if locator.endswith("/data.xml"):
                return b"HTTP 300 Multiple Choices"
            return None

        def close(self) -> None:
            pass

    monkeypatch.setitem(
        sys.modules,
        "farchive",
        SimpleNamespace(Farchive=FakeFarchive),
    )

    row = uk_broad_baseline.score_one("ukpga/1945/9")

    assert row["score_status"] == "source_frontier"
    assert row["source_frontier_reason"] == "oracle_multiple_choices"
    assert row["base_source_status"] == "available"
    assert row["oracle_source_status"] == "multiple_choices"
    assert "error" not in row


def test_score_one_reports_both_metadata_only_as_base_and_oracle_source_frontier(
    monkeypatch,
) -> None:
    _install_score_one_archive(
        monkeypatch,
        enacted_xml=_uk_metadata_only_statute_xml(),
        current_xml=_uk_metadata_only_statute_xml(),
    )

    row = uk_broad_baseline.score_one("ukpga/1824/74")
    annotated = uk_broad_baseline._annotate_row_work_selection(dict(row))

    assert row["score_status"] == "source_frontier"
    assert row["source_frontier_reason"] == "base_and_oracle_metadata_only"
    assert row["base_source_status"] == "metadata_only"
    assert row["oracle_source_status"] == "metadata_only"
    assert row["base_source_locator"].endswith("/ukpga/1824/74/enacted/data.xml")
    assert row["oracle_source_locator"].endswith("/ukpga/1824/74/data.xml")
    assert row["base_source_witness"]["source_role"] == "uk_broad_base_source"
    assert row["oracle_source_witness"]["source_role"] == "uk_broad_oracle_source"
    assert row["base_source_witness"]["source_lane"] == "enacted_xml"
    assert row["oracle_source_witness"]["source_lane"] == "current_xml"
    assert row["base_source_witness"]["source_status"] == "metadata_only"
    assert row["oracle_source_witness"]["source_status"] == "metadata_only"
    assert row["base_source_witness_digest_coverage"] == (
        "artifact_and_preview_digest"
    )
    assert row["oracle_source_witness_digest_coverage"] == (
        "artifact_and_preview_digest"
    )
    work_item = annotated["source_frontier_work_item"]
    assert work_item["frontier_family"] == (
        "uk_source_frontier_base_and_oracle_metadata_only"
    )
    assert work_item["frontier_status"] == "source_footing_gap"
    assert work_item["authorization_status"] == "source_footing_gap"
    assert work_item["executable"] is False
    assert work_item["replay_authorized"] is False
    assert work_item["source_witness"]["source_role"] == "uk_broad_base_source"
    assert work_item["required_proofs"] == [
        "source_identity",
        "official_source_body_or_accepted_source_pathology",
    ]
    assert "metadata_only_xml_as_executable_text" in work_item["forbidden_shortcuts"]
    assert work_item["detail"]["truth_claim"] == (
        "source_frontier_work_item_not_replay_authorization"
    )
    assert "error" not in row


def test_score_one_reports_base_metadata_only_before_oracle_source_state(
    monkeypatch,
) -> None:
    _install_score_one_archive(
        monkeypatch,
        enacted_xml=_uk_metadata_only_statute_xml(),
        current_xml=_uk_available_statute_xml(),
    )

    row = uk_broad_baseline.score_one("ukpga/1851/28")

    assert row["score_status"] == "source_frontier"
    assert row["source_frontier_reason"] == "base_metadata_only"
    assert row["base_source_status"] == "metadata_only"
    assert row["oracle_source_status"] == "available"
    assert row["base_source_witness"]["source_status"] == "metadata_only"
    assert row["oracle_source_witness"]["source_status"] == "available"
    assert row["base_source_witness"]["digest"]
    assert row["oracle_source_witness"]["digest"]
    assert "error" not in row


def test_score_one_reports_oracle_metadata_only_when_base_is_available(
    monkeypatch,
) -> None:
    _install_score_one_archive(
        monkeypatch,
        enacted_xml=_uk_available_statute_xml(),
        current_xml=_uk_metadata_only_statute_xml(),
    )

    row = uk_broad_baseline.score_one("ukpga/1859/57")

    assert row["score_status"] == "source_frontier"
    assert row["source_frontier_reason"] == "oracle_metadata_only"
    assert row["base_source_status"] == "available"
    assert row["oracle_source_status"] == "metadata_only"
    assert "error" not in row


def test_score_one_reports_compile_phase_timings(monkeypatch) -> None:
    xml = _uk_available_statute_xml()
    _install_score_one_archive(monkeypatch, enacted_xml=xml, current_xml=xml)

    class FakeNode:
        def __init__(self, eid: str) -> None:
            self.attrs = {"eId": eid}
            self.children = []
            self.text = "Text."

    class FakePipeline:
        def __init__(self, _repo_root) -> None:
            pass

        def compile_ops_for_statute(
            self,
            _statute_id,
            *,
            compile_phase_timings_out=None,
            **_kwargs,
        ):
            assert compile_phase_timings_out is not None
            compile_phase_timings_out["compile_load_effects"] = 0.125
            compile_phase_timings_out["compile_lower_effect"] = 0.25
            return []

        def apply_ops(self, base_ir, _ops, **_kwargs):
            return base_ir

    fake_ir = SimpleNamespace(body=FakeNode("section-1"), supplements=[])

    import lawvm.uk_legislation.effects as effects
    import lawvm.uk_legislation.source_adjudication as source_adjudication
    import lawvm.uk_legislation.uk_amendment_replay as uk_amendment_replay
    import lawvm.uk_legislation.uk_grafter as uk_grafter

    monkeypatch.setattr(effects, "load_effects_for_statute_from_archive", lambda *_args: [])
    monkeypatch.setattr(
        uk_amendment_replay,
        "UKDiagnosticReplayFilterMode",
        SimpleNamespace(OBSERVE_ONLY="observe_only"),
    )
    monkeypatch.setattr(uk_amendment_replay, "UKReplayPipeline", FakePipeline)
    monkeypatch.setattr(
        uk_grafter,
        "extract_eid_map_bytes",
        lambda *_args: {
            "eid_map": {"section-1": "section-1"},
            "text_map": {},
            "physical_eid_aliases": {},
            "visible_number_eid_aliases": {},
        },
    )
    monkeypatch.setattr(uk_grafter, "parse_uk_statute_ir_bytes", lambda *_args, **_kwargs: fake_ir)
    monkeypatch.setattr(uk_grafter, "_clean_num", lambda value: str(value).strip("()"))
    monkeypatch.setattr(
        source_adjudication,
        "normalize_uk_replay_compare_eids",
        lambda replay, oracle, **_kwargs: (set(replay), set(oracle)),
    )
    monkeypatch.setattr(source_adjudication, "_normalize_uk_source_container_eid", lambda eid: eid)

    row = uk_broad_baseline.score_one("ukpga/2000/1")

    assert row["score_status"] == "scored"
    assert row["compile_wall_seconds"] >= 0
    assert row["compile_phase_timings"] == {
        "compile_load_effects": 0.125,
        "compile_lower_effect": 0.25,
    }
    assert "error" not in row


def test_metadata_only_source_frontiers_are_pathology_not_non_manual_chain() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "ukpga/1824/74",
                "score_status": "source_frontier",
                "source_frontier_reason": "base_and_oracle_metadata_only",
            },
            {
                "statute_id": "ukpga/1851/28",
                "score_status": "source_frontier",
                "source_frontier_reason": "base_metadata_only",
            },
        ]
    )

    assert summary["source_chain_frontier_reasons"] == {
        "base_and_oracle_metadata_only": 1,
        "base_metadata_only": 1,
    }
    assert summary["non_manual_source_chain_frontier_count"] == 0
    assert summary["non_manual_source_chain_frontier_statutes"] == []
    assert summary["source_or_oracle_pathology_frontier_count"] == 2
    assert summary["source_or_oracle_pathology_frontier_reasons"] == {
        "base_and_oracle_metadata_only": 1,
        "base_metadata_only": 1,
    }
    assert summary["source_frontier_source_witness_role_counts"] == {
        "base:__missing__": 2,
        "oracle:__missing__": 2,
    }
    assert summary["source_frontier_source_witness_digest_coverage_counts"] == {
        "base:missing_source_witness": 2,
        "oracle:missing_source_witness": 2,
    }
    assert summary["source_frontier_work_item_family_counts"] == {
        "__missing_work_item__": 2,
    }
    assert summary["source_frontier_work_item_authorization_status_counts"] == {
        "__missing_work_item__": 2,
    }
    assert summary["source_frontier_work_item_missing_proof_counts"] == {
        "__missing_work_item__": 2,
    }


def test_source_frontier_summary_counts_source_witness_coverage() -> None:
    row = uk_broad_baseline._annotate_row_work_selection(
        {
            "statute_id": "ukpga/1824/74",
            "score_status": "source_frontier",
            "source_frontier_reason": "base_and_oracle_metadata_only",
            "base_source_witness": {
                "source_role": "uk_broad_base_source",
                "digest": "base-digest",
                "preview_digest": "base-preview",
            },
            "oracle_source_witness": {
                "source_role": "uk_broad_oracle_source",
                "digest": "oracle-digest",
            },
        }
    )
    summary = uk_broad_baseline.summarize_results([row])

    assert summary["source_frontier_source_witness_role_counts"] == {
        "base:uk_broad_base_source": 1,
        "oracle:uk_broad_oracle_source": 1,
    }
    assert summary["source_frontier_source_witness_digest_coverage_counts"] == {
        "base:artifact_and_preview_digest": 1,
        "oracle:artifact_digest": 1,
    }
    assert summary["source_frontier_work_item_family_counts"] == {
        "uk_source_frontier_base_and_oracle_metadata_only": 1,
    }
    assert summary["source_frontier_work_item_authorization_status_counts"] == {
        "source_footing_gap": 1,
    }
    assert summary["source_frontier_work_item_missing_proof_counts"] == {
        "official_source_body_or_accepted_source_pathology": 1,
        "source_identity": 1,
    }


def test_sample_statutes_excludes_multiple_choices_and_includes_regnal_leaves(
    monkeypatch,
) -> None:
    xml = b"""<?xml version="1.0"?>
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation"
    NumberOfProvisions="1">
  <Body><P1 id="section-1"><Pnumber>1</Pnumber><P1para>Text.</P1para></P1></Body>
</Legislation>"""
    marker = b"HTTP 300 Multiple Choices"
    data = {
        "https://www.legislation.gov.uk/ukpga/1955/18/enacted/data.xml": marker,
        "https://www.legislation.gov.uk/ukpga/1955/18/data.xml": marker,
        "https://www.legislation.gov.uk/ukpga/Eliz2/3-4/18/enacted/data.xml": xml,
        "https://www.legislation.gov.uk/ukpga/Eliz2/3-4/18/data.xml": xml,
        "https://www.legislation.gov.uk/ukpga/2000/1/enacted/data.xml": xml,
        "https://www.legislation.gov.uk/ukpga/2000/1/data.xml": xml,
        "https://www.legislation.gov.uk/ukpga/2000/1/2020-01-01/data.xml": xml,
    }

    class FakeFarchive:
        def __init__(self, _path, **_kwargs):
            pass

        def locators(self, pattern: str) -> list[str]:
            if pattern.endswith("/enacted/data.xml"):
                return [loc for loc in data if loc.endswith("/enacted/data.xml")]
            return [loc for loc in data if loc.endswith("/data.xml")]

        def get(self, locator: str) -> bytes | None:
            return data.get(locator)

        def close(self) -> None:
            pass

    monkeypatch.setitem(
        sys.modules,
        "farchive",
        SimpleNamespace(Farchive=FakeFarchive),
    )

    sample = set(uk_broad_baseline.sample_statutes(10, 1, classes=["ukpga"]))

    assert "ukpga/1955/18" not in sample
    assert sample == {"ukpga/2000/1", "ukpga/Eliz2/3-4/18"}


def test_normalized_compare_eids_uses_uk_misses_compare_lens() -> None:
    replay, oracle = uk_broad_baseline._normalized_compare_eids(
        {"section-1", "p00090"},
        {"section-1"},
        oracle_physical_eid_aliases={},
        oracle_visible_number_eid_aliases={},
    )

    assert replay == {"section-1"}
    assert oracle == {"section-1"}


def test_oracle_only_addition_change_id_evidence_uses_compare_aliases() -> None:
    current_xml = b"""<?xml version="1.0"?>
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation"
    NumberOfProvisions="1">
  <Body>
    <P1 id="section-1">
      <Pnumber>1</Pnumber>
      <P1para>
        <P2 id="section-1-2-a-i">
          <Pnumber>(i)</Pnumber>
          <P2para><Addition ChangeId="d30p77">Inserted text.</Addition></P2para>
        </P2>
      </P1para>
    </P1>
  </Body>
</Legislation>"""

    evidence = uk_broad_baseline._oracle_only_addition_change_id_evidence(
        current_xml=current_xml,
        oracle_only_eids={"section-1-2-a-1"},
        oracle_physical_eid_aliases={},
        oracle_visible_number_eid_aliases={
            "section-1-2-a-i": "section-1-2-a-1",
        },
        compiled_change_ids={"key-effect"},
    )

    assert evidence == {
        "n_oracle_only_addition_eids": 1,
        "oracle_only_addition_eid_samples": ["section-1-2-a-1"],
        "oracle_only_addition_change_ids": ["d30p77"],
        "n_oracle_only_uncompiled_addition_eids": 1,
        "oracle_only_uncompiled_addition_eid_samples": ["section-1-2-a-1"],
        "oracle_only_uncompiled_addition_change_ids": ["d30p77"],
    }


def test_summarize_results_counts_frontiers_and_zero_oracle_retention() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "ukpga/1938/22",
                "score_status": "scored",
                "aligned": 0.0,
                "n_replay": 420,
                "n_oracle": 0,
                "n_zero_oracle_retention_eids": 420,
            },
            {
                "statute_id": "ukpga/1992/41",
                "score_status": "scored",
                "aligned": 64.0,
                "aligned_excluding_grounding_collateral": 98.7,
                "n_grounding_collateral": 169,
                "n_replay": 469,
                "n_oracle": 304,
            },
            {
                "statute_id": "ukpga/1986/61",
                "score_status": "scored",
                "aligned": 50.9,
                "aligned_excluding_grounding_collateral": 50.9,
                "n_grounding_collateral": 100,
                "n_replay": 389,
                "n_oracle": 568,
                "manual_frontier_status_counts": {
                    "deterministic_frontend_candidate": 2,
                    "manual_compile_candidate": 3,
                    "source_or_feed_target_conflict": 1,
                },
                "manual_frontier_rule_counts": {
                    "uk_manual_frontier_amount_specified_source_target_mismatch": 1,
                    "uk_manual_frontier_parser_or_extraction_candidate": 2,
                    "uk_manual_frontier_repeal_table_candidate": 3,
                },
                "manual_frontier_owner_phase_counts": {
                    "affecting_source_extraction": 3,
                    "typed_elaboration": 3,
                },
                "manual_frontier_rule_owner_phase_counts": {
                    "affecting_source_extraction:uk_manual_frontier_amount_specified_source_target_mismatch": 1,
                    "affecting_source_extraction:uk_manual_frontier_parser_or_extraction_candidate": 2,
                    "typed_elaboration:uk_manual_frontier_repeal_table_candidate": 3,
                },
                "compile_rejection_owner_phase_counts": {
                    "affecting_source_extraction": 4,
                    "typed_elaboration": 1,
                },
                "compile_rejection_authorization_status_counts": {
                    "effect_feed_parse_diagnostic_evidence_only": 4,
                    "lowering_compile_blocked": 1,
                },
                "compile_rejection_missing_proof_counts": {
                    "canonical_operation_compilation": 1,
                    "canonical_operation_or_replay_authorization": 4,
                },
                "compile_rejection_rule_owner_phase_counts": {
                    "affecting_source_extraction:uk_effect_source_gap": 4,
                    "typed_elaboration:uk_effect_target_gap": 1,
                },
                "blocking_compile_rejection_owner_phase_counts": {
                    "typed_elaboration": 1,
                },
                "blocking_compile_rejection_authorization_status_counts": {
                    "lowering_compile_blocked": 1,
                },
                "blocking_compile_rejection_missing_proof_counts": {
                    "canonical_operation_compilation": 1,
                },
                "blocking_compile_rejection_rule_owner_phase_counts": {
                    "typed_elaboration:uk_effect_target_gap": 1,
                },
                "manual_frontier_manual_compile_candidate_rule_counts": {
                    "uk_manual_frontier_repeal_table_candidate": 3,
                },
                "manual_frontier_manual_compile_candidate_rule_owner_phase_counts": {
                    "typed_elaboration:uk_manual_frontier_repeal_table_candidate": 3,
                },
                "manual_frontier_deterministic_candidate_rule_counts": {
                    "uk_manual_frontier_parser_or_extraction_candidate": 2,
                },
                "manual_frontier_deterministic_candidate_rule_owner_phase_counts": {
                    "affecting_source_extraction:uk_manual_frontier_parser_or_extraction_candidate": 2,
                },
                "manual_frontier_template_status_counts": {
                    "available": 4,
                    "not_available": 2,
                },
                "manual_frontier_template_gap_status_counts": {
                    "deterministic_frontend_candidate": 2,
                },
                "manual_frontier_template_gap_rule_counts": {
                    "uk_manual_frontier_parser_or_extraction_candidate": 2,
                },
                "n_mutation_events": 4,
                "n_mutation_boundary_reports": 4,
                "n_mutation_boundary_unexplained_reports": 1,
                "n_mutation_boundary_unexplained_paths": 2,
                "mutation_boundary_result_code_counts": {
                    "REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET": 1,
                    "REPLAY_MISSING_PRIMARY_TARGET_CONSUMPTION": 1,
                },
                "mutation_boundary_helper_counts": {
                    "apply_op": 3,
                    "replace_text": 1,
                },
                "mutation_boundary_proof_status_counts": {
                    "proved": 2,
                    "unresolved": 1,
                    "violated": 1,
                },
                "mutation_boundary_proof_rule_counts": {
                    "mutation_boundary_path_set_proved": 2,
                    "mutation_boundary_path_set_unresolved": 1,
                    "mutation_boundary_path_set_violated": 1,
                },
                "mutation_boundary_proof_owner_phase_counts": {
                    "replay_invariants": 4,
                },
            },
            {
                "statute_id": "ukpga/1961/60",
                "score_status": "scored",
                "aligned": 22.7,
                "aligned_excluding_grounding_collateral": 22.7,
                "unaligned": 22.7,
                "n_grounding_collateral": 0,
                "n_replay": 5,
                "n_oracle": 22,
                "base_source_status": "metadata_only",
            },
            {
                "statute_id": "eur/2019/1841",
                "score_status": "scored",
                "aligned": 61.8,
                "aligned_excluding_grounding_collateral": 61.8,
                "unaligned": 100.0,
                "n_grounding_collateral": 0,
                "n_replay": 34,
                "n_oracle": 21,
            },
            {
                "statute_id": "uksi/2000/1043",
                "score_status": "scored",
                "aligned": 77.7,
                "aligned_excluding_grounding_collateral": 77.7,
                "unaligned": 75.3,
                "n_grounding_collateral": 1,
                "n_replay": 168,
                "n_oracle": 215,
                "n_ops": 0,
            },
            {
                "statute_id": "ukpga/1945/9",
                "score_status": "source_frontier",
                "source_frontier_reason": "base_too_small",
            },
            {
                "statute_id": "ukpga/1945/10",
                "score_status": "source_frontier",
                "source_frontier_reason": "base_too_small",
            },
            {
                "statute_id": "ukpga/1946/1",
                "error": "RuntimeError: boom",
            },
        ]
    )

    assert len(summary["scored"]) == 6
    assert len(summary["errored"]) == 1
    assert len(summary["source_frontier"]) == 2
    assert summary["source_frontier_reasons"] == {"base_too_small": 2}
    assert summary["source_chain_frontier_reasons"] == {
        "base_too_small": 2,
        "effect_rows_absent_or_unpublished": 1,
    }
    assert summary["source_chain_frontier_statutes"] == {
        "base_too_small": ["ukpga/1945/10", "ukpga/1945/9"],
        "effect_rows_absent_or_unpublished": ["uksi/2000/1043"],
    }
    assert summary["non_manual_source_chain_frontier_count"] == 1
    assert summary["non_manual_source_chain_frontier_statutes"] == ["uksi/2000/1043"]
    assert summary["source_or_oracle_pathology_frontier_count"] == 2
    assert summary["source_or_oracle_pathology_frontier_statutes"] == [
        "ukpga/1945/10",
        "ukpga/1945/9",
    ]
    assert summary["source_or_oracle_pathology_frontier_reasons"] == {
        "base_too_small": 2
    }
    assert summary["source_or_oracle_pathology_frontier_reason_statutes"] == {
        "base_too_small": ["ukpga/1945/10", "ukpga/1945/9"]
    }
    assert summary["zero_oracle_retention_count"] == 1
    assert summary["zero_oracle_retention_statutes"] == ["ukpga/1938/22"]
    assert summary["zero_oracle_retention_eids"] == 420
    assert summary["zero_oracle_retention_reasons"] == {
        "oracle_current_projection_no_structural_eids": 1
    }
    assert summary["zero_oracle_retention_reason_statutes"] == {
        "oracle_current_projection_no_structural_eids": ["ukpga/1938/22"]
    }
    assert summary["comparison_core_count"] == 2
    assert summary["comparison_core_statutes"] == [
        "ukpga/1986/61",
        "ukpga/1992/41",
    ]
    assert summary["comparison_core_mean_aligned"] == 57.45
    assert summary["comparison_core_mean_aligned_excluding_grounding_collateral"] == 74.8
    assert summary["comparison_non_core_count"] == 4
    assert summary["comparison_non_core_bucket_counts"] == {
        "base_metadata_only_frontier": 1,
        "no_effect_rows_frontier": 1,
        "structural_match_eid_scheme_residual": 1,
        "zero_oracle_retention": 1,
    }
    assert summary["comparison_non_core_statutes"] == [
        "eur/2019/1841",
        "ukpga/1938/22",
        "ukpga/1961/60",
        "uksi/2000/1043",
    ]
    assert summary["triage_buckets"] == {
        "base_metadata_only_frontier": 1,
        "error": 1,
        "high_fidelity_after_grounding": 1,
        "no_effect_rows_frontier": 1,
        "residual_after_grounding": 1,
        "source_frontier:base_too_small": 2,
        "structural_match_eid_scheme_residual": 1,
        "zero_oracle_retention": 1,
    }
    assert summary["triage_bucket_statutes"] == {
        "base_metadata_only_frontier": ["ukpga/1961/60"],
        "error": ["ukpga/1946/1"],
        "high_fidelity_after_grounding": ["ukpga/1992/41"],
        "no_effect_rows_frontier": ["uksi/2000/1043"],
        "residual_after_grounding": ["ukpga/1986/61"],
        "source_frontier:base_too_small": ["ukpga/1945/10", "ukpga/1945/9"],
        "structural_match_eid_scheme_residual": ["eur/2019/1841"],
        "zero_oracle_retention": ["ukpga/1938/22"],
    }
    assert summary["agreement_residual_family_counts"] == {
        "agreement": 1,
        "error": 1,
        "non_commensurable_surface": 2,
        "replay_bug": 1,
        "source_footing_gap": 3,
        "topology_granularity_mismatch": 1,
    }
    assert summary["agreement_residual_status_counts"] == {
        "error": 1,
        "frontier": 5,
        "residual": 3,
    }
    assert summary["agreement_residual_owner_phase_counts"] == {
        "affecting_source_extraction": 2,
        "compare_oracle_classification": 4,
        "effect_metadata_frontend": 1,
        "replay_invariants": 1,
        "source_pathology_manual_frontier": 1,
    }
    assert summary["agreement_residual_rule_counts"] == {
        "uk_broad_base_metadata_only_frontier": 1,
        "uk_broad_error": 1,
        "uk_broad_high_fidelity_after_grounding": 1,
        "uk_broad_no_effect_rows_frontier": 1,
        "uk_broad_residual_after_grounding": 1,
        "uk_broad_source_frontier:base_too_small": 2,
        "uk_broad_structural_match_eid_scheme_residual": 1,
        "uk_broad_zero_oracle_retention": 1,
    }
    assert summary["active_unclassified_residual_count"] == 2
    assert summary["active_unclassified_residual_statutes"] == [
        "eur/2019/1841",
        "ukpga/1986/61",
    ]
    assert summary["deterministic_frontend_candidate_count"] == 2
    assert summary["deterministic_frontend_candidate_statutes"] == [
        "ukpga/1986/61",
    ]
    assert summary["manual_frontier_status_counts"] == {
        "deterministic_frontend_candidate": 2,
        "manual_compile_candidate": 3,
        "source_or_feed_target_conflict": 1,
    }
    assert summary["manual_frontier_rule_counts"] == {
        "uk_manual_frontier_amount_specified_source_target_mismatch": 1,
        "uk_manual_frontier_parser_or_extraction_candidate": 2,
        "uk_manual_frontier_repeal_table_candidate": 3,
    }
    assert summary["manual_frontier_owner_phase_counts"] == {
        "affecting_source_extraction": 3,
        "typed_elaboration": 3,
    }
    assert summary["manual_frontier_rule_owner_phase_counts"] == {
        "affecting_source_extraction:uk_manual_frontier_amount_specified_source_target_mismatch": 1,
        "affecting_source_extraction:uk_manual_frontier_parser_or_extraction_candidate": 2,
        "typed_elaboration:uk_manual_frontier_repeal_table_candidate": 3,
    }
    assert summary["compile_rejection_owner_phase_counts"] == {
        "affecting_source_extraction": 4,
        "typed_elaboration": 1,
    }
    assert summary["compile_rejection_authorization_status_counts"] == {
        "effect_feed_parse_diagnostic_evidence_only": 4,
        "lowering_compile_blocked": 1,
    }
    assert summary["compile_rejection_missing_proof_counts"] == {
        "canonical_operation_compilation": 1,
        "canonical_operation_or_replay_authorization": 4,
    }
    assert summary["compile_rejection_rule_owner_phase_counts"] == {
        "affecting_source_extraction:uk_effect_source_gap": 4,
        "typed_elaboration:uk_effect_target_gap": 1,
    }
    assert summary["blocking_compile_rejection_owner_phase_counts"] == {
        "typed_elaboration": 1,
    }
    assert summary["blocking_compile_rejection_authorization_status_counts"] == {
        "lowering_compile_blocked": 1,
    }
    assert summary["blocking_compile_rejection_missing_proof_counts"] == {
        "canonical_operation_compilation": 1,
    }
    assert summary["blocking_compile_rejection_rule_owner_phase_counts"] == {
        "typed_elaboration:uk_effect_target_gap": 1,
    }
    assert summary["manual_frontier_manual_compile_candidate_rule_counts"] == {
        "uk_manual_frontier_repeal_table_candidate": 3,
    }
    assert summary[
        "manual_frontier_manual_compile_candidate_rule_owner_phase_counts"
    ] == {
        "typed_elaboration:uk_manual_frontier_repeal_table_candidate": 3,
    }
    assert summary["manual_frontier_deterministic_candidate_rule_counts"] == {
        "uk_manual_frontier_parser_or_extraction_candidate": 2,
    }
    assert summary[
        "manual_frontier_deterministic_candidate_rule_owner_phase_counts"
    ] == {
        "affecting_source_extraction:uk_manual_frontier_parser_or_extraction_candidate": 2,
    }
    assert summary["manual_frontier_template_status_counts"] == {
        "available": 4,
        "not_available": 2,
    }
    assert summary["manual_frontier_template_gap_status_counts"] == {
        "deterministic_frontend_candidate": 2,
    }
    assert summary["manual_frontier_template_gap_rule_counts"] == {
        "uk_manual_frontier_parser_or_extraction_candidate": 2,
    }
    assert summary["manual_frontier_template_gap_count"] == 2
    assert summary["mutation_boundary_event_count"] == 4
    assert summary["mutation_boundary_report_count"] == 4
    assert summary["mutation_boundary_unexplained_report_count"] == 1
    assert summary["mutation_boundary_unexplained_path_count"] == 2
    assert summary["mutation_boundary_result_code_counts"] == {
        "REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET": 1,
        "REPLAY_MISSING_PRIMARY_TARGET_CONSUMPTION": 1,
    }
    assert summary["mutation_boundary_helper_counts"] == {
        "apply_op": 3,
        "replace_text": 1,
    }
    assert summary["mutation_boundary_proof_status_counts"] == {
        "proved": 2,
        "unresolved": 1,
        "violated": 1,
    }
    assert summary["mutation_boundary_proof_rule_counts"] == {
        "mutation_boundary_path_set_proved": 2,
        "mutation_boundary_path_set_unresolved": 1,
        "mutation_boundary_path_set_violated": 1,
    }
    assert summary["mutation_boundary_proof_owner_phase_counts"] == {
        "replay_invariants": 4,
    }
    assert summary["mutation_boundary_unexplained_statutes"] == [
        "ukpga/1986/61",
    ]


def test_broad_baseline_report_envelope_declares_agreement_scope(tmp_path) -> None:
    snapshot_path = tmp_path / "snapshot.json"
    report = uk_broad_baseline.uk_broad_baseline_report_jsonable(
        [
            {
                "statute_id": "ukpga/1992/41",
                "score_status": "scored",
                "aligned": 98.7,
                "aligned_excluding_grounding_collateral": 98.7,
                "unaligned": 98.7,
                "n_replay": 10,
                "n_oracle": 10,
            },
            {
                "statute_id": "ukpga/1945/9",
                "score_status": "source_frontier",
                "source_frontier_reason": "base_too_small",
            },
        ],
        ids=["ukpga/1992/41", "ukpga/1945/9"],
        snapshot_path=snapshot_path,
    )

    assert report["jurisdiction"] == "uk"
    assert report["report_kind"] == "uk_broad_baseline_agreement_report"
    assert report["schema"] == "lawvm.uk_broad_baseline_agreement_report.v1"
    assert (
        report["truth_claim"]
        == "uk_replay_oracle_agreement_regression_guard_not_source_truth"
    )
    assert report["replay_claims"] is True
    assert report["agreement_claims"] is True
    assert report["canonical_effect_claims"] is False
    assert report["candidate_effect_claims"] is False
    assert report["dry_run_claims"] is False
    assert report["summary"]["scored_count"] == 1
    assert report["summary"]["source_frontier_count"] == 1
    assert report["summary"]["manual_frontier_template_gap_count"] == 0
    assert report["summary"]["zero_oracle_retention_statutes"] == []
    assert report["summary"]["agreement_residual_family_counts"] == {
        "agreement": 1,
        "source_footing_gap": 1,
    }
    assert report["filters"]["snapshot_path"] == str(snapshot_path)
    assert report["written_paths"] == [str(snapshot_path)]
    assert "agreement_as_execution_authorization" in report["forbidden_shortcuts"]
    assert "mutation_boundary_proof" in report["next_promotion_requires"]
    assert len(report["rows"]) == 2
    assert report["rows"][0]["agreement_residual"]["family"] == "agreement"
    assert report["rows"][0]["agreement_residual"]["agreement_residual_status"] == "residual"
    assert report["rows"][1]["agreement_residual"]["family"] == "source_footing_gap"


def test_mutation_boundary_diagnostics_reports_unexplained_paths() -> None:
    diagnostics = uk_broad_baseline._mutation_boundary_diagnostics(
        [
            MutationEvent(
                op_id="op-1",
                source_statute="ukpga/2000/1",
                action="replace",
                helper="replace_text",
                outcome="replaced_node",
                resolved_target_path=(("body", ""), ("section", "1")),
                replaced_paths=((("body", ""), ("section", "2")),),
            )
        ]
    )

    assert diagnostics["n_mutation_events"] == 1
    assert diagnostics["n_mutation_boundary_reports"] == 1
    assert diagnostics["n_mutation_boundary_unexplained_reports"] == 1
    assert diagnostics["n_mutation_boundary_unexplained_paths"] == 1
    assert diagnostics["mutation_boundary_result_code_counts"] == {
        "REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET": 1,
    }
    assert diagnostics["mutation_boundary_helper_counts"] == {"replace_text": 1}
    assert diagnostics["mutation_boundary_proof_status_counts"] == {"violated": 1}
    assert diagnostics["mutation_boundary_proof_rule_counts"] == {
        "mutation_boundary_path_set_violated": 1,
    }
    assert diagnostics["mutation_boundary_proof_owner_phase_counts"] == {
        "replay_invariants": 1,
    }
    assert diagnostics["mutation_boundary_unexplained_samples"] == [
        {
            "op_id": "op-1",
            "helper": "replace_text",
            "outcome": "replaced_node",
            "result_codes": ["REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET"],
            "unexplained_paths": ["body/section:2"],
        }
    ]
    assert diagnostics["mutation_boundary_proof_samples"] == [
        {
            "proof_id": "uk-broad-mutation-boundary:0:op-1",
            "jurisdiction": "uk",
            "materialization_surface": "uk_broad_baseline_replay",
            "operation_id": "op-1",
            "owner_phase": "replay_invariants",
            "rule_id": "mutation_boundary_path_set_violated",
            "boundary_proof_status": "violated",
            "helper": "replace_text",
            "outcome": "replaced_node",
            "selected_target_paths": ["body/section:1"],
            "allowed_mutation_regions": ["body/section:1"],
            "changed_paths": ["body/section:2"],
            "covered_changed_paths": [],
            "unexplained_changed_paths": ["body/section:2"],
            "declared_allowance_paths": [],
            "declared_recovery_paths": [],
            "declared_recovery_rule_ids": [],
            "declared_migration_paths": [],
            "declared_migration_rule_ids": [],
            "matched_allowance_rule_ids": [],
            "result_codes": ["REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET"],
            "path_set_invariant_holds": False,
            "safe_default": "treat_unproved_boundary_as_replay_invariant_residual",
            "forbidden_shortcuts": [
                "ignore_unexplained_changed_paths",
                "use_oracle_agreement_as_boundary_proof",
                "broaden_target_region_after_replay",
            ],
            "detail": {},
        }
    ]


def test_retained_repeal_oracle_targets_maps_retained_eu_article_aliases() -> None:
    ops = [
        LegalOperation(
            op_id="key-article",
            sequence=1,
            action=StructuralAction.REPEAL,
            target=LegalAddress(path=(("section", "1"),)),
        ),
        LegalOperation(
            op_id="key-annex",
            sequence=2,
            action=StructuralAction.REPEAL,
            target=LegalAddress(path=(("schedule", "2"),)),
        ),
    ]

    targets = uk_broad_baseline._retained_repeal_oracle_targets(
        ops,
        {"article-1", "annex-ii"},
        statute_id="eur/2020/2220",
    )

    assert targets == ["annex-ii", "article-1"]


def test_summarize_results_counts_grounding_dominated_residuals() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "eur/2019/2018",
                "score_status": "scored",
                "aligned": 17.4,
                "aligned_excluding_grounding_collateral": 41.0,
                "n_grounding_collateral": 165,
                "n_replay": 287,
                "n_oracle": 62,
            },
        ]
    )

    assert summary["triage_buckets"] == {"grounding_dominated_residual": 1}
    assert summary["active_unclassified_residual_count"] == 1


def test_summarize_results_counts_effect_feed_absent_frontier() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "uksi/2000/1043",
                "score_status": "scored",
                "aligned": 77.7,
                "aligned_excluding_grounding_collateral": 77.7,
                "unaligned": 75.4,
                "n_grounding_collateral": 0,
                "n_replay": 167,
                "n_oracle": 215,
                "n_ops": 0,
                "n_only_in_oracle": 48,
                "n_only_in_replayed": 0,
                "compile_rejection_rule_counts": {
                    "uk_effect_feed_pages_absent_recorded": 1,
                },
                "n_blocking_compile_rejections": 0,
            },
        ]
    )

    assert summary["triage_buckets"] == {"effect_feed_absent_frontier": 1}
    assert summary["source_chain_frontier_reasons"] == {"effect_feed_pages_absent": 1}
    assert summary["source_chain_frontier_statutes"] == {
        "effect_feed_pages_absent": ["uksi/2000/1043"],
    }
    assert summary["non_manual_source_chain_frontier_count"] == 0
    assert summary["non_manual_source_chain_frontier_statutes"] == []


def test_summarize_results_counts_no_effect_rows_frontier() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "ukpga/1976/83",
                "score_status": "scored",
                "aligned": 83.7,
                "aligned_excluding_grounding_collateral": 83.7,
                "unaligned": 30.2,
                "n_grounding_collateral": 0,
                "n_replay": 123,
                "n_oracle": 147,
                "n_effects": 0,
                "n_ops": 0,
                "n_compile_rejections": 0,
                "n_blocking_compile_rejections": 0,
            },
        ]
    )

    assert summary["triage_buckets"] == {"no_effect_rows_frontier": 1}
    assert summary["source_chain_frontier_reasons"] == {
        "effect_rows_absent_or_unpublished": 1
    }
    assert summary["source_chain_frontier_statutes"] == {
        "effect_rows_absent_or_unpublished": ["ukpga/1976/83"],
    }


def test_summarize_results_classifies_temporal_commencement_frontier() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "ukpga/1978/9",
                "score_status": "scored",
                "aligned": 82.57,
                "aligned_excluding_grounding_collateral": 82.57,
                "unaligned": 80.73,
                "n_grounding_collateral": 0,
                "n_replay": 90,
                "n_oracle": 109,
                "n_only_in_oracle": 19,
                "n_only_in_replayed": 0,
                "n_effects": 1,
                "n_ops": 1,
                "compile_rejection_rule_counts": {
                    "uk_effect_undated_applied_si_commencement_unresolved": 1,
                },
                "manual_frontier_status_counts": {
                    "deterministic_frontend_supported": 1,
                },
            },
        ]
    )

    assert summary["triage_buckets"] == {"temporal_commencement_frontier": 1}
    assert summary["agreement_residual_family_counts"] == {"source_footing_gap": 1}
    assert summary["agreement_residual_owner_phase_counts"] == {
        "effect_metadata_frontend": 1
    }
    assert summary["active_unclassified_residual_count"] == 0


def test_summarize_results_does_not_frontier_source_backed_commencement_date() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "ukpga/1978/9",
                "score_status": "scored",
                "aligned": 82.57,
                "aligned_excluding_grounding_collateral": 82.57,
                "unaligned": 80.73,
                "n_grounding_collateral": 0,
                "n_replay": 90,
                "n_oracle": 109,
                "n_only_in_oracle": 19,
                "n_only_in_replayed": 0,
                "n_effects": 1,
                "n_ops": 1,
                "n_compile_rejections": 1,
                "n_blocking_compile_rejections": 0,
                "compile_rejection_rule_counts": {
                    "uk_effect_undated_applied_si_commencement_date": 1,
                },
                "manual_frontier_status_counts": {
                    "deterministic_frontend_supported": 1,
                },
            },
        ]
    )

    assert summary["triage_buckets"] == {
        "source_backed_temporal_recovery_oracle_residual": 1
    }
    assert summary["active_unclassified_residual_count"] == 0
    assert summary["agreement_residual_family_counts"] == {
        "temporal_mismatch": 1
    }
    assert summary["agreement_residual_owner_phase_counts"] == {
        "compare_oracle_classification": 1
    }
    assert "agreement_residual_missing_proof_counts" not in summary


def test_summarize_results_classifies_oracle_expansion_without_effects() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "ukpga/1959/49",
                "score_status": "scored",
                "aligned": 29.33,
                "aligned_excluding_grounding_collateral": 29.33,
                "unaligned": 28.67,
                "n_grounding_collateral": 0,
                "n_replay": 44,
                "n_oracle": 150,
                "n_only_in_oracle": 106,
                "n_only_in_replayed": 0,
                "n_effects": 8,
                "n_ops": 8,
                "n_oracle_only_schedule_eids": 100,
                "has_schedule_targeting_ops": False,
                "manual_frontier_status_counts": {
                    "deterministic_frontend_supported": 8,
                },
            },
        ]
    )

    assert summary["triage_buckets"] == {"oracle_expansion_without_effects": 1}
    assert summary["agreement_residual_family_counts"] == {
        "source_footing_gap": 1,
    }
    assert summary["agreement_residual_status_counts"] == {"frontier": 1}
    assert summary["agreement_residual_owner_phase_counts"] == {
        "effect_metadata_frontend": 1,
    }
    assert summary["active_unclassified_residual_count"] == 0


def test_summarize_results_classifies_oracle_addition_source_chain_frontier() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "ukpga/1934/41",
                "score_status": "scored",
                "aligned": 73.91,
                "aligned_excluding_grounding_collateral": 73.91,
                "unaligned": 73.91,
                "base_source_has_body": True,
                "oracle_source_has_body": True,
                "n_replay": 17,
                "n_oracle": 23,
                "n_only_in_oracle": 6,
                "n_only_in_replayed": 0,
                "n_effects": 2,
                "n_ops": 2,
                "n_compile_rejections": 2,
                "n_blocking_compile_rejections": 0,
                "n_oracle_only_uncompiled_addition_eids": 6,
                "oracle_only_uncompiled_addition_change_ids": [
                    "d30p157",
                    "d30p193",
                    "d30p61",
                    "d30p77",
                ],
                "manual_frontier_status_counts": {
                    "deterministic_frontend_supported": 2,
                },
            },
        ]
    )

    assert summary["triage_buckets"] == {
        "oracle_addition_source_chain_frontier": 1,
    }
    assert summary["source_chain_frontier_reasons"] == {
        "oracle_addition_changeid_source_chain_gap": 1,
    }
    assert summary["empty_effect_feed_frontier_count"] == 1
    assert summary["non_manual_source_chain_frontier_count"] == 0
    assert summary["comparison_non_core_bucket_counts"] == {
        "oracle_addition_source_chain_frontier": 1,
    }
    assert summary["agreement_residual_family_counts"] == {
        "source_footing_gap": 1,
    }
    assert summary["agreement_residual_status_counts"] == {"frontier": 1}
    assert summary["agreement_residual_owner_phase_counts"] == {
        "effect_metadata_frontend": 1,
    }
    assert summary["active_unclassified_residual_count"] == 0


def test_oracle_addition_source_chain_frontier_requires_full_miss_coverage() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "ukpga/1934/41",
                "score_status": "scored",
                "aligned": 73.91,
                "aligned_excluding_grounding_collateral": 73.91,
                "unaligned": 73.91,
                "base_source_has_body": True,
                "oracle_source_has_body": True,
                "n_replay": 17,
                "n_oracle": 23,
                "n_only_in_oracle": 6,
                "n_only_in_replayed": 0,
                "n_effects": 2,
                "n_ops": 2,
                "n_compile_rejections": 2,
                "n_blocking_compile_rejections": 0,
                "n_oracle_only_uncompiled_addition_eids": 5,
                "oracle_only_uncompiled_addition_change_ids": ["d30p77"],
                "manual_frontier_status_counts": {
                    "deterministic_frontend_supported": 2,
                },
            },
        ]
    )

    assert summary["triage_buckets"] == {"residual_after_grounding": 1}
    assert summary["active_unclassified_residual_count"] == 1


def test_oracle_addition_source_chain_frontier_does_not_preempt_specific_buckets() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "ukpga/1849/67",
                "score_status": "scored",
                "aligned": 66.67,
                "aligned_excluding_grounding_collateral": 66.67,
                "base_source_has_body": True,
                "oracle_source_has_body": True,
                "base_source_number_of_provisions": 2,
                "oracle_source_number_of_provisions": 3,
                "n_replay": 2,
                "n_oracle": 3,
                "n_only_in_oracle": 1,
                "n_only_in_replayed": 0,
                "n_blocking_compile_rejections": 0,
                "oracle_only_eid_samples": ["section-1"],
                "n_oracle_only_uncompiled_addition_eids": 1,
                "oracle_only_uncompiled_addition_change_ids": ["d1"],
            },
            {
                "statute_id": "ukpga/1956/70",
                "score_status": "scored",
                "aligned": 88.89,
                "aligned_excluding_grounding_collateral": 88.89,
                "oracle_source_has_body": True,
                "n_replay": 24,
                "n_oracle": 27,
                "n_only_in_oracle": 3,
                "n_only_in_replayed": 0,
                "n_blocking_compile_rejections": 0,
                "n_oracle_only_uncompiled_addition_eids": 3,
                "oracle_only_uncompiled_addition_change_ids": ["d2"],
            },
        ]
    )

    assert summary["triage_buckets"] == {
        "body_oracle_first_paragraph_sectionization_residual": 1,
        "bounded_low_volume_residual": 1,
    }
    assert summary["source_chain_frontier_reasons"] == {}
    assert summary["active_unclassified_residual_count"] == 0


def test_summarize_results_does_not_classify_schedule_expansion_when_ops_target_schedule() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "ukpga/1959/49",
                "score_status": "scored",
                "aligned": 29.33,
                "aligned_excluding_grounding_collateral": 29.33,
                "unaligned": 28.67,
                "n_grounding_collateral": 0,
                "n_replay": 44,
                "n_oracle": 150,
                "n_only_in_oracle": 106,
                "n_only_in_replayed": 0,
                "n_effects": 8,
                "n_ops": 8,
                "n_oracle_only_schedule_eids": 100,
                "has_schedule_targeting_ops": True,
                "manual_frontier_status_counts": {
                    "deterministic_frontend_supported": 8,
                },
            },
        ]
    )

    assert summary["triage_buckets"] == {"residual_after_grounding": 1}
    assert summary["agreement_residual_family_counts"] == {"replay_bug": 1}
    assert summary["active_unclassified_residual_count"] == 1


def test_summarize_results_counts_empty_effect_feed_frontier() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "uksi/2012/1206",
                "score_status": "scored",
                "aligned": 88.6,
                "aligned_excluding_grounding_collateral": 88.6,
                "unaligned": 88.6,
                "n_grounding_collateral": 0,
                "n_replay": 31,
                "n_oracle": 35,
                "n_effects": 0,
                "n_ops": 0,
                "compile_rejection_rule_counts": {
                    "uk_effect_feed_empty_recorded": 1,
                },
                "n_blocking_compile_rejections": 0,
            },
        ]
    )

    assert summary["triage_buckets"] == {"no_effect_rows_frontier": 1}
    assert summary["source_chain_frontier_reasons"] == {"effect_feed_empty": 1}
    assert summary["source_chain_frontier_statutes"] == {
        "effect_feed_empty": ["uksi/2012/1206"],
    }
    assert summary["non_manual_source_chain_frontier_count"] == 0
    assert summary["non_manual_source_chain_frontier_statutes"] == []
    assert summary["empty_effect_feed_frontier_count"] == 1
    assert summary["empty_effect_feed_frontier_statutes"] == ["uksi/2012/1206"]


def test_summarize_results_counts_nonreplay_effect_frontier() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "ukpga/1901/7",
                "score_status": "scored",
                "aligned": 91.7,
                "aligned_excluding_grounding_collateral": 91.7,
                "unaligned": 32.8,
                "n_grounding_collateral": 0,
                "n_replay": 22,
                "n_oracle": 24,
                "n_effects": 1,
                "n_ops": 0,
                "n_compile_rejections": 1,
                "n_blocking_compile_rejections": 0,
                "compile_rejection_rule_counts": {
                    "uk_effect_missing_structural_payload_rejected": 1,
                },
            },
        ]
    )

    assert summary["triage_buckets"] == {"nonreplay_effect_frontier": 1}
    assert summary["source_chain_frontier_reasons"] == {
        "effect_rows_missing_structural_payload": 1
    }
    assert summary["source_chain_frontier_statutes"] == {
        "effect_rows_missing_structural_payload": ["ukpga/1901/7"],
    }


def test_summarize_results_routes_manual_compile_nonreplay_frontier() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "ukpga/1933/19",
                "score_status": "scored",
                "aligned": 76.47,
                "aligned_excluding_grounding_collateral": 76.47,
                "unaligned": 10.45,
                "n_grounding_collateral": 0,
                "n_replay": 26,
                "n_oracle": 34,
                "n_effects": 1,
                "n_ops": 0,
                "n_compile_rejections": 1,
                "n_blocking_compile_rejections": 1,
                "compile_rejection_rule_counts": {
                    "uk_effect_repeal_table_quoted_words_text_repeal_unresolved": 1,
                },
                "manual_frontier_status_counts": {
                    "manual_compile_candidate": 1,
                },
            },
        ]
    )

    assert summary["triage_buckets"] == {"nonreplay_effect_frontier": 1}
    assert summary["source_chain_frontier_reasons"] == {
        "manual_frontier_manual_compile_candidate": 1
    }
    assert summary["source_chain_frontier_statutes"] == {
        "manual_frontier_manual_compile_candidate": ["ukpga/1933/19"],
    }
    assert summary["non_manual_source_chain_frontier_count"] == 0
    assert summary["non_manual_source_chain_frontier_statutes"] == []


def test_summarize_results_splits_non_admitted_replay_lens_rows() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "uksi/2009/3023",
                "score_status": "scored",
                "aligned": 60.0,
                "aligned_excluding_grounding_collateral": 60.0,
                "unaligned": 80.0,
                "n_grounding_collateral": 0,
                "n_replay": 3,
                "n_oracle": 5,
                "n_effects": 1,
                "n_ops": 0,
                "n_compile_rejections": 1,
                "n_blocking_compile_rejections": 0,
                "compile_rejection_rule_counts": {
                    "uk_effect_missing_structural_payload_rejected": 1,
                },
                "manual_frontier_status_counts": {
                    "non_textual_or_out_of_scope": 1,
                },
            },
        ]
    )

    assert summary["triage_buckets"] == {"nonreplay_effect_frontier": 1}
    assert summary["source_chain_frontier_reasons"] == {
        "effect_rows_not_admitted_by_replay_lens": 1
    }
    assert summary["source_chain_frontier_statutes"] == {
        "effect_rows_not_admitted_by_replay_lens": ["uksi/2009/3023"],
    }
    assert summary["non_manual_source_chain_frontier_count"] == 0
    assert summary["non_manual_source_chain_frontier_statutes"] == []
    assert summary["replay_lens_frontier_count"] == 1
    assert summary["replay_lens_frontier_statutes"] == ["uksi/2009/3023"]


def test_summarize_results_splits_mixed_replay_lens_and_source_insufficient_rows() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "ukpga/1868/119",
                "score_status": "scored",
                "aligned": 91.4,
                "aligned_excluding_grounding_collateral": 91.4,
                "unaligned": 27.6,
                "n_grounding_collateral": 0,
                "n_replay": 32,
                "n_oracle": 35,
                "n_effects": 29,
                "n_ops": 0,
                "n_compile_rejections": 48,
                "n_blocking_compile_rejections": 1,
                "compile_rejection_rule_counts": {
                    "uk_effect_lowering_no_supported_action_rejected": 27,
                    "uk_effect_structural_pseudo_definition_target_rejected": 1,
                },
                "manual_frontier_status_counts": {
                    "non_textual_or_out_of_scope": 28,
                    "source_insufficient": 1,
                },
            },
        ]
    )

    assert summary["triage_buckets"] == {"nonreplay_effect_frontier": 1}
    assert summary["source_chain_frontier_reasons"] == {
        "effect_rows_not_admitted_by_replay_lens": 1,
        "manual_frontier_source_insufficient": 1,
    }
    assert summary["source_chain_frontier_statutes"] == {
        "effect_rows_not_admitted_by_replay_lens": ["ukpga/1868/119"],
        "manual_frontier_source_insufficient": ["ukpga/1868/119"],
    }
    assert summary["non_manual_source_chain_frontier_count"] == 0
    assert summary["non_manual_source_chain_frontier_statutes"] == []
    assert summary["replay_lens_frontier_count"] == 1
    assert summary["replay_lens_frontier_statutes"] == ["ukpga/1868/119"]


def test_summarize_results_counts_compile_rejection_dominated_residuals() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "ukpga/1986/61",
                "score_status": "scored",
                "aligned": 50.9,
                "aligned_excluding_grounding_collateral": 50.9,
                "unaligned": 50.5,
                "n_grounding_collateral": 100,
                "n_replay": 389,
                "n_oracle": 568,
                "n_only_in_oracle": 279,
                "n_only_in_replayed": 11,
                "n_compile_rejections": 168,
                "n_blocking_compile_rejections": 90,
                "compile_rejection_rule_counts": {
                    "uk_effect_lowering_no_supported_action_rejected": 28,
                    "uk_effect_repeal_table_structural_repeal": 62,
                },
            },
        ]
    )

    assert summary["triage_buckets"] == {"compile_rejection_dominated_residual": 1}


def test_summarize_results_classifies_retained_repeal_oracle_branch() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "ukpga/1973/33",
                "score_status": "scored",
                "aligned": 50.0,
                "aligned_excluding_grounding_collateral": 50.0,
                "unaligned": 50.0,
                "n_grounding_collateral": 0,
                "n_replay": 19,
                "n_oracle": 38,
                "n_only_in_oracle": 19,
                "n_only_in_replayed": 0,
                "n_ops": 1,
                "n_compile_rejections": 0,
                "n_blocking_compile_rejections": 0,
                "retained_repeal_oracle_targets": ["section-1"],
                "n_retained_repeal_oracle_targets": 1,
            },
        ]
    )

    assert summary["triage_buckets"] == {"retained_repeal_oracle_branch": 1}
    assert summary["active_unclassified_residual_count"] == 0


def test_retained_repeal_oracle_targets_include_whole_act_repeal() -> None:
    op = LegalOperation(
        op_id="uk_test_whole_act_repeal",
        sequence=0,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(), special=FacetKind.WHOLE_ACT),
    )

    targets = uk_broad_baseline._retained_repeal_oracle_targets(
        [op],
        {"section-1", "section-2"},
        statute_id="ukpga/1997/4",
    )

    assert targets == ["/whole_act"]


def test_summarize_results_classifies_whole_act_repeal_oracle_branch() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "ukpga/1997/4",
                "score_status": "scored",
                "aligned": 0.0,
                "aligned_excluding_grounding_collateral": 0.0,
                "unaligned": 0.0,
                "n_grounding_collateral": 0,
                "n_replay": 0,
                "n_oracle": 9,
                "n_only_in_oracle": 9,
                "n_only_in_replayed": 0,
                "n_ops": 1,
                "n_compile_rejections": 2,
                "n_blocking_compile_rejections": 0,
                "retained_repeal_oracle_targets": ["/whole_act"],
                "n_retained_repeal_oracle_targets": 1,
            },
        ]
    )

    assert summary["triage_buckets"] == {"retained_repeal_oracle_branch": 1}
    assert summary["agreement_residual_family_counts"] == {
        "oracle_editorial_pathology": 1,
    }
    assert summary["active_unclassified_residual_count"] == 0


def test_summarize_results_counts_retained_eu_mixed_representation_residuals() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "eur/2019/1021",
                "score_status": "scored",
                "aligned": 51.2,
                "aligned_excluding_grounding_collateral": 53.2,
                "unaligned": 23.6,
                "n_grounding_collateral": 49,
                "n_replay": 211,
                "n_oracle": 203,
                "n_only_in_oracle": 95,
                "n_only_in_replayed": 103,
                "n_compile_rejections": 171,
                "n_blocking_compile_rejections": 127,
            },
        ]
    )

    assert summary["triage_buckets"] == {
        "retained_eu_mixed_representation_residual": 1
    }


def test_summarize_results_counts_retained_eu_schedule_oracle_granularity() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "eur/2020/1231",
                "score_status": "scored",
                "aligned": 82.83,
                "aligned_excluding_grounding_collateral": 82.83,
                "unaligned": 79.8,
                "base_source_has_body": False,
                "base_source_has_schedules": True,
                "oracle_source_has_body": False,
                "oracle_source_has_schedules": True,
                "n_grounding_collateral": 0,
                "n_replay": 82,
                "n_oracle": 99,
                "n_only_in_oracle": 17,
                "n_only_in_replayed": 0,
                "n_effects": 20,
                "n_ops": 20,
                "n_compile_rejections": 11,
                "n_blocking_compile_rejections": 0,
                "manual_frontier_status_counts": {
                    "deterministic_frontend_supported": 20,
                },
            },
        ]
    )

    assert summary["triage_buckets"] == {
        "retained_eu_schedule_oracle_granularity_residual": 1
    }
    assert summary["agreement_residual_family_counts"] == {
        "topology_granularity_mismatch": 1,
    }
    assert summary["active_unclassified_residual_count"] == 0


def test_summarize_results_counts_bounded_low_volume_residuals() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "ukpga/1976/38",
                "score_status": "scored",
                "aligned": 91.9,
                "aligned_excluding_grounding_collateral": 91.9,
                "unaligned": 91.9,
                "n_grounding_collateral": 0,
                "n_replay": 91,
                "n_oracle": 99,
                "n_only_in_oracle": 8,
                "n_only_in_replayed": 0,
                "n_compile_rejections": 11,
                "n_blocking_compile_rejections": 5,
            },
        ]
    )

    assert summary["triage_buckets"] == {"bounded_low_volume_residual": 1}
    assert summary["active_unclassified_residual_count"] == 0


def test_summarize_results_routes_body_nested_list_oracle_granularity() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "ukpga/1939/82",
                "score_status": "scored",
                "aligned": 89.66,
                "aligned_excluding_grounding_collateral": 89.66,
                "unaligned": 51.11,
                "base_source_has_body": True,
                "base_source_has_schedules": False,
                "oracle_source_has_body": True,
                "oracle_source_has_schedules": False,
                "n_grounding_collateral": 0,
                "n_replay": 26,
                "n_oracle": 29,
                "n_only_in_oracle": 3,
                "n_only_in_replayed": 0,
                "n_effects": 1,
                "n_ops": 1,
                "n_compile_rejections": 1,
                "n_blocking_compile_rejections": 0,
                "oracle_only_eid_samples": [
                    "section-8-1-a-1",
                    "section-8-1-a-2",
                    "section-8-1-a-3",
                ],
                "manual_frontier_status_counts": {
                    "deterministic_frontend_supported": 1,
                },
            },
        ]
    )

    assert summary["triage_buckets"] == {
        "body_nested_list_oracle_granularity_residual": 1
    }
    assert summary["agreement_residual_family_counts"] == {
        "topology_granularity_mismatch": 1,
    }
    assert summary["agreement_residual_owner_phase_counts"] == {
        "compare_oracle_classification": 1,
    }
    assert summary["active_unclassified_residual_count"] == 0


def test_body_nested_list_oracle_granularity_requires_eid_samples() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "ukpga/1939/82",
                "score_status": "scored",
                "aligned": 89.66,
                "aligned_excluding_grounding_collateral": 89.66,
                "unaligned": 51.11,
                "base_source_has_body": True,
                "base_source_has_schedules": False,
                "oracle_source_has_body": True,
                "oracle_source_has_schedules": False,
                "n_grounding_collateral": 0,
                "n_replay": 26,
                "n_oracle": 29,
                "n_only_in_oracle": 3,
                "n_only_in_replayed": 0,
                "n_compile_rejections": 1,
                "n_blocking_compile_rejections": 0,
            },
        ]
    )

    assert summary["triage_buckets"] == {"bounded_low_volume_residual": 1}


def test_summarize_results_routes_body_oracle_collapsed_range_granularity() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "ukpga/1933/19",
                "score_status": "scored",
                "aligned": 76.47,
                "aligned_excluding_grounding_collateral": 76.47,
                "unaligned": 10.45,
                "base_source_has_body": True,
                "base_source_has_schedules": True,
                "oracle_source_has_body": True,
                "oracle_source_has_schedules": True,
                "n_grounding_collateral": 0,
                "n_replay": 26,
                "n_oracle": 34,
                "n_only_in_oracle": 8,
                "n_only_in_replayed": 0,
                "n_effects": 1,
                "n_ops": 1,
                "n_compile_rejections": 1,
                "n_blocking_compile_rejections": 0,
                "oracle_only_eid_samples": [
                    "part-1",
                    "schedule-6",
                    "schedule-6-part-i",
                    "schedule-6-part-ii",
                    "section-1320",
                    "section-2224",
                    "section-2734",
                    "section-58",
                ],
                "manual_frontier_status_counts": {
                    "deterministic_frontend_supported": 1,
                },
            },
        ]
    )

    assert summary["triage_buckets"] == {
        "body_oracle_collapsed_range_granularity_residual": 1
    }
    assert summary["agreement_residual_family_counts"] == {
        "topology_granularity_mismatch": 1,
    }
    assert summary["agreement_residual_owner_phase_counts"] == {
        "compare_oracle_classification": 1,
    }
    assert summary["active_unclassified_residual_count"] == 0


def test_body_oracle_collapsed_range_granularity_requires_unambiguous_range_sample() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "ukpga/1933/19",
                "score_status": "scored",
                "aligned": 91.0,
                "aligned_excluding_grounding_collateral": 91.0,
                "unaligned": 91.0,
                "base_source_has_body": True,
                "oracle_source_has_body": True,
                "n_grounding_collateral": 0,
                "n_replay": 91,
                "n_oracle": 99,
                "n_only_in_oracle": 1,
                "n_only_in_replayed": 0,
                "n_compile_rejections": 1,
                "n_blocking_compile_rejections": 0,
                "oracle_only_eid_samples": ["section-58"],
            },
        ]
    )

    assert summary["triage_buckets"] == {"bounded_low_volume_residual": 1}


def test_summarize_results_routes_body_oracle_first_paragraph_sectionization() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "ukpga/1842/97",
                "score_status": "scored",
                "aligned": 80.0,
                "aligned_excluding_grounding_collateral": 80.0,
                "unaligned": 0.0,
                "base_source_has_body": True,
                "base_source_has_schedules": False,
                "base_source_number_of_provisions": "5",
                "oracle_source_has_body": True,
                "oracle_source_has_schedules": False,
                "oracle_source_number_of_provisions": "6",
                "n_grounding_collateral": 0,
                "n_replay": 4,
                "n_oracle": 5,
                "n_only_in_oracle": 1,
                "n_only_in_replayed": 0,
                "n_effects": 1,
                "n_ops": 1,
                "n_compile_rejections": 1,
                "n_blocking_compile_rejections": 0,
                "oracle_only_eid_samples": ["section-1."],
                "manual_frontier_status_counts": {
                    "deterministic_frontend_supported": 1,
                },
            },
        ]
    )

    assert summary["triage_buckets"] == {
        "body_oracle_first_paragraph_sectionization_residual": 1
    }
    assert summary["agreement_residual_family_counts"] == {
        "topology_granularity_mismatch": 1,
    }
    assert summary["agreement_residual_status_counts"] == {"frontier": 1}
    assert summary["agreement_residual_owner_phase_counts"] == {
        "compare_oracle_classification": 1,
    }
    assert summary["active_unclassified_residual_count"] == 0


def test_body_oracle_first_paragraph_sectionization_requires_exact_first_section_sample() -> None:
    for oracle_only_sample in ("section-1", "section-1."):
        summary = uk_broad_baseline.summarize_results(
            [
                {
                    "statute_id": "ukpga/1842/97",
                    "score_status": "scored",
                    "aligned": 80.0,
                    "aligned_excluding_grounding_collateral": 80.0,
                    "unaligned": 0.0,
                    "base_source_has_body": True,
                    "base_source_has_schedules": False,
                    "base_source_number_of_provisions": 5,
                    "oracle_source_has_body": True,
                    "oracle_source_has_schedules": False,
                    "oracle_source_number_of_provisions": 6,
                    "n_grounding_collateral": 0,
                    "n_replay": 4,
                    "n_oracle": 5,
                    "n_only_in_oracle": 1,
                    "n_only_in_replayed": 0,
                    "n_compile_rejections": 1,
                    "n_blocking_compile_rejections": 0,
                    "oracle_only_eid_samples": [oracle_only_sample],
                },
            ]
        )

        assert summary["triage_buckets"] == {
            "body_oracle_first_paragraph_sectionization_residual": 1
        }


def test_body_oracle_first_paragraph_sectionization_rejects_later_section_sample() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "ukpga/1842/97",
                "score_status": "scored",
                "aligned": 80.0,
                "aligned_excluding_grounding_collateral": 80.0,
                "unaligned": 0.0,
                "base_source_has_body": True,
                "base_source_has_schedules": False,
                "base_source_number_of_provisions": 5,
                "oracle_source_has_body": True,
                "oracle_source_has_schedules": False,
                "oracle_source_number_of_provisions": 6,
                "n_grounding_collateral": 0,
                "n_replay": 4,
                "n_oracle": 5,
                "n_only_in_oracle": 1,
                "n_only_in_replayed": 0,
                "n_compile_rejections": 1,
                "n_blocking_compile_rejections": 0,
                "oracle_only_eid_samples": ["section-2"],
            },
        ]
    )

    assert summary["triage_buckets"] == {"residual_after_grounding": 1}
    assert summary["active_unclassified_residual_count"] == 1


def test_summarize_results_routes_low_volume_manual_frontier_residuals() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "ukpga/1887/55",
                "score_status": "scored",
                "aligned": 86.67,
                "aligned_excluding_grounding_collateral": 86.67,
                "unaligned": 86.67,
                "n_grounding_collateral": 0,
                "n_replay": 91,
                "n_oracle": 105,
                "n_only_in_oracle": 14,
                "n_only_in_replayed": 0,
                "n_compile_rejections": 16,
                "n_blocking_compile_rejections": 16,
                "blocking_compile_rejection_rule_counts": {
                    "uk_effect_table_entry_instruction_rejected": 10,
                    "uk_effect_repeal_table_structural_repeal_unresolved": 3,
                },
            },
        ]
    )

    assert summary["triage_buckets"] == {"manual_compile_frontier_residual": 1}
    assert summary["active_unclassified_residual_count"] == 0


def test_summarize_results_routes_large_manual_frontier_residuals() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "ukpga/1990/8",
                "score_status": "scored",
                "aligned": 83.81,
                "aligned_excluding_grounding_collateral": 83.81,
                "unaligned": 74.4,
                "n_grounding_collateral": 0,
                "n_replay": 6906,
                "n_oracle": 8240,
                "n_only_in_oracle": 1334,
                "n_only_in_replayed": 0,
                "n_compile_rejections": 3655,
                "n_blocking_compile_rejections": 130,
                "manual_frontier_status_counts": {
                    "manual_compile_candidate": 46,
                    "source_insufficient": 72,
                    "deterministic_frontend_supported": 1548,
                },
                "blocking_compile_rejection_rule_counts": {
                    "uk_effect_schedule_note_target_rejected": 16,
                    "uk_effect_table_entry_instruction_rejected": 8,
                },
            },
        ]
    )

    assert summary["triage_buckets"] == {"manual_compile_frontier_residual": 1}


def test_summarize_results_aggregates_manual_frontier_authorization() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "ukpga/1990/8",
                "score_status": "scored",
                "aligned": 83.81,
                "aligned_excluding_grounding_collateral": 83.81,
                "unaligned": 74.4,
                "n_grounding_collateral": 0,
                "n_replay": 6906,
                "n_oracle": 8240,
                "n_only_in_oracle": 1334,
                "n_only_in_replayed": 0,
                "n_compile_rejections": 3655,
                "n_blocking_compile_rejections": 130,
                "manual_frontier_authorization_status_counts": {
                    "manual_claim_required": 2,
                    "source_insufficient": 1,
                },
                "manual_frontier_authorization_status_owner_phase_counts": {
                    "typed_elaboration:manual_claim_required": 2,
                    "affecting_source_extraction:source_insufficient": 1,
                },
                "manual_frontier_missing_proof_counts": {
                    "mutation_boundary_proof": 2,
                    "official_source_witness": 1,
                },
                "manual_frontier_work_item_family_counts": {
                    "uk_manual_frontier_heading_facet_candidate": 2,
                    "uk_manual_frontier_source_insufficient": 1,
                },
                "manual_frontier_work_item_authorization_status_counts": {
                    "manual_claim_required": 2,
                    "source_insufficient": 1,
                },
                "manual_frontier_work_item_candidate_operation_family_counts": {
                    "facet_text_rewrite": 2,
                    "source_chain_text_patch": 1,
                },
                "manual_frontier_work_item_required_validator_check_counts": {
                    "changed_paths_are_within_declared_facet_target": 2,
                    "changed_paths_are_within_text_patch_target": 1,
                    "claim_preserves_effect_feed_target_identity": 1,
                },
                "manual_frontier_work_item_source_witness_role_counts": {
                    "affecting_source_fragment": 2,
                    "effect_feed_row": 1,
                },
                "manual_frontier_work_item_source_witness_digest_coverage_counts": {
                    "artifact_and_preview_digest": 1,
                    "preview_digest": 2,
                },
                "manual_frontier_work_item_missing_candidate_operation_family_count": 4,
                "manual_frontier_work_item_missing_required_validator_checks_count": 5,
            },
        ]
    )

    assert summary["manual_frontier_authorization_status_counts"] == {
        "manual_claim_required": 2,
        "source_insufficient": 1,
    }
    assert summary["manual_frontier_authorization_status_owner_phase_counts"] == {
        "affecting_source_extraction:source_insufficient": 1,
        "typed_elaboration:manual_claim_required": 2,
    }
    assert summary["manual_frontier_missing_proof_counts"] == {
        "mutation_boundary_proof": 2,
        "official_source_witness": 1,
    }
    assert summary["manual_frontier_work_item_family_counts"] == {
        "uk_manual_frontier_heading_facet_candidate": 2,
        "uk_manual_frontier_source_insufficient": 1,
    }
    assert summary["manual_frontier_work_item_authorization_status_counts"] == {
        "manual_claim_required": 2,
        "source_insufficient": 1,
    }
    assert summary["manual_frontier_work_item_candidate_operation_family_counts"] == {
        "facet_text_rewrite": 2,
        "source_chain_text_patch": 1,
    }
    assert summary["manual_frontier_work_item_required_validator_check_counts"] == {
        "changed_paths_are_within_declared_facet_target": 2,
        "changed_paths_are_within_text_patch_target": 1,
        "claim_preserves_effect_feed_target_identity": 1,
    }
    assert summary["manual_frontier_work_item_source_witness_role_counts"] == {
        "affecting_source_fragment": 2,
        "effect_feed_row": 1,
    }
    assert summary["manual_frontier_work_item_source_witness_digest_coverage_counts"] == {
        "artifact_and_preview_digest": 1,
        "preview_digest": 2,
    }
    assert summary["manual_frontier_work_item_missing_candidate_operation_family_count"] == 4
    assert summary["manual_frontier_work_item_missing_required_validator_checks_count"] == 5


def test_zero_oracle_retention_reasons_surface_effect_feed_gaps() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "ukpga/1979/6",
                "score_status": "scored",
                "aligned": 0.0,
                "n_replay": 72,
                "n_oracle": 0,
                "n_zero_oracle_retention_eids": 72,
                "oracle_source_status": "available",
                "oracle_source_has_body": True,
                "compile_rejection_rule_counts": {
                    "uk_effect_feed_empty_recorded": 1,
                },
            },
            {
                "statute_id": "uksi/2006/3221",
                "score_status": "scored",
                "aligned": 0.0,
                "n_replay": 280,
                "n_oracle": 0,
                "n_zero_oracle_retention_eids": 280,
                "oracle_source_status": "available",
                "compile_rejection_rule_counts": {
                    "uk_effect_feed_pages_absent_recorded": 1,
                },
            },
        ]
    )

    assert summary["zero_oracle_retention_reasons"] == {
        "effect_feed_empty": 1,
        "effect_feed_pages_absent": 1,
        "oracle_current_projection_no_live_eids": 1,
        "oracle_current_projection_no_structural_eids": 1,
    }
    assert summary["zero_oracle_retention_reason_statutes"] == {
        "effect_feed_empty": ["ukpga/1979/6"],
        "effect_feed_pages_absent": ["uksi/2006/3221"],
        "oracle_current_projection_no_live_eids": ["ukpga/1979/6"],
        "oracle_current_projection_no_structural_eids": ["uksi/2006/3221"],
    }
    assert summary["source_chain_frontier_reasons"] == {
        "effect_feed_empty": 1,
        "effect_feed_pages_absent": 1,
    }
    assert summary["non_manual_source_chain_frontier_count"] == 0


def test_compile_rejection_bucket_ignores_nonblocking_observations() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "ukpga/1998/17",
                "score_status": "scored",
                "aligned": 88.8,
                "aligned_excluding_grounding_collateral": 88.8,
                "unaligned": 82.1,
                "n_grounding_collateral": 42,
                "n_replay": 1314,
                "n_oracle": 1424,
                "n_only_in_oracle": 159,
                "n_only_in_replayed": 49,
                "n_compile_rejections": 314,
                "n_blocking_compile_rejections": 7,
            },
        ]
    )

    assert summary["triage_buckets"] == {"residual_after_grounding": 1}


def test_triage_bucket_for_row_is_added_to_one_row_output(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        uk_broad_baseline,
        "score_one",
        lambda _statute_id: {
            "statute_id": "ukpga/1961/60",
            "score_status": "scored",
            "aligned": 22.7,
            "aligned_excluding_grounding_collateral": 22.7,
            "unaligned": 22.7,
            "n_replay": 5,
            "n_oracle": 22,
            "base_source_status": "metadata_only",
        },
    )

    assert uk_broad_baseline.main(["--one", "ukpga/1961/60"]) == 0
    row = json.loads(capsys.readouterr().out)

    assert row["triage_bucket"] == "base_metadata_only_frontier"
    assert row["source_chain_frontier"] is False
    assert row["source_chain_frontier_reason"] == ""
    assert row["source_chain_frontier_reasons"] == []


def test_source_chain_frontier_reason_is_added_to_one_row_output(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        uk_broad_baseline,
        "score_one",
        lambda _statute_id: {
            "statute_id": "ukpga/1976/83",
            "score_status": "scored",
            "aligned": 83.7,
            "aligned_excluding_grounding_collateral": 83.7,
            "unaligned": 30.2,
            "n_replay": 123,
            "n_oracle": 147,
            "n_effects": 0,
            "n_ops": 0,
        },
    )

    assert uk_broad_baseline.main(["--one", "ukpga/1976/83"]) == 0
    row = json.loads(capsys.readouterr().out)

    assert row["triage_bucket"] == "no_effect_rows_frontier"
    assert row["source_chain_frontier"] is True
    assert row["source_chain_frontier_reason"] == "effect_rows_absent_or_unpublished"
    assert row["source_chain_frontier_reasons"] == [
        "effect_rows_absent_or_unpublished"
    ]


def test_source_chain_frontier_reason_reports_empty_effect_feed(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        uk_broad_baseline,
        "score_one",
        lambda _statute_id: {
            "statute_id": "uksi/2012/1206",
            "score_status": "scored",
            "aligned": 88.6,
            "aligned_excluding_grounding_collateral": 88.6,
            "unaligned": 88.6,
            "n_replay": 31,
            "n_oracle": 35,
            "n_effects": 0,
            "n_ops": 0,
            "compile_rejection_rule_counts": {
                "uk_effect_feed_empty_recorded": 1,
            },
        },
    )

    assert uk_broad_baseline.main(["--one", "uksi/2012/1206"]) == 0
    row = json.loads(capsys.readouterr().out)

    assert row["triage_bucket"] == "no_effect_rows_frontier"
    assert row["source_chain_frontier"] is True
    assert row["source_chain_frontier_reason"] == "effect_feed_empty"
    assert row["source_chain_frontier_reasons"] == ["effect_feed_empty"]


def test_source_chain_frontier_marks_source_insufficient_manual_rows() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "uksi/2000/1043",
                "score_status": "scored",
                "aligned": 88.53,
                "aligned_excluding_grounding_collateral": 88.53,
                "unaligned": 88.53,
                "n_grounding_collateral": 0,
                "n_replay": 168,
                "n_oracle": 215,
                "n_only_in_oracle": 47,
                "n_only_in_replayed": 0,
                "n_effects": 54,
                "n_ops": 37,
                "manual_frontier_status_counts": {
                    "deterministic_frontend_supported": 32,
                    "source_insufficient": 19,
                },
            },
        ]
    )

    assert summary["triage_buckets"] == {"manual_compile_frontier_residual": 1}
    assert summary["source_chain_frontier_reasons"] == {
        "manual_frontier_source_insufficient": 1
    }
    assert summary["source_chain_frontier_statutes"] == {
        "manual_frontier_source_insufficient": ["uksi/2000/1043"],
    }


def test_source_chain_frontier_marks_text_patch_chain_gap_manual_rows() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "ukpga/1954/21",
                "score_status": "scored",
                "aligned": 70.0,
                "aligned_excluding_grounding_collateral": 70.0,
                "unaligned": 70.0,
                "n_grounding_collateral": 0,
                "n_replay": 28,
                "n_oracle": 40,
                "n_only_in_oracle": 12,
                "n_only_in_replayed": 0,
                "n_effects": 18,
                "n_ops": 8,
                "manual_frontier_status_counts": {
                    "deterministic_frontend_supported": 3,
                    "non_textual_or_out_of_scope": 10,
                },
                "manual_frontier_rule_counts": {
                    "uk_manual_frontier_text_patch_preimage_chain_gap": 1,
                    "uk_manual_frontier_text_patch_postimage_chain_gap": 2,
                    "uk_manual_frontier_text_patch_target_source_chain_gap": 1,
                },
                "manual_frontier_work_item_candidate_operation_family_counts": {
                    "source_chain_text_patch": 4,
                },
            },
        ]
    )

    assert summary["triage_buckets"] == {"manual_compile_frontier_residual": 1}
    assert summary["active_unclassified_residual_count"] == 0
    assert summary["source_chain_frontier_reasons"] == {
        "effect_rows_not_admitted_by_replay_lens": 1,
        "manual_frontier_source_chain_text_patch_gap": 1,
    }
    assert summary["source_chain_frontier_statutes"] == {
        "effect_rows_not_admitted_by_replay_lens": ["ukpga/1954/21"],
        "manual_frontier_source_chain_text_patch_gap": ["ukpga/1954/21"],
    }
    assert summary["non_manual_source_chain_frontier_count"] == 0
    assert summary["non_manual_source_chain_frontier_statutes"] == []


def test_replay_lens_residual_allows_replay_authorized_rows() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "ukpga/1954/21",
                "score_status": "scored",
                "aligned": 70.0,
                "aligned_excluding_grounding_collateral": 70.0,
                "unaligned": 70.0,
                "n_grounding_collateral": 0,
                "n_replay": 28,
                "n_oracle": 40,
                "n_only_in_oracle": 12,
                "n_only_in_replayed": 0,
                "n_effects": 17,
                "n_ops": 4,
                "manual_frontier_status_counts": {
                    "deterministic_frontend_supported": 4,
                    "non_textual_or_out_of_scope": 13,
                },
                "manual_frontier_rule_counts": {
                    "uk_manual_frontier_deterministic_supported": 4,
                    "uk_manual_frontier_external_act_target_out_of_scope": 1,
                    "uk_manual_frontier_non_textual_or_out_of_scope": 12,
                },
                "manual_frontier_work_item_candidate_operation_family_counts": {
                    "non_textual_or_out_of_scope": 13,
                },
            },
        ]
    )

    assert summary["triage_buckets"] == {"manual_compile_frontier_residual": 1}
    assert summary["active_unclassified_residual_count"] == 0
    assert summary["source_chain_frontier_reasons"] == {
        "effect_rows_not_admitted_by_replay_lens": 1
    }
    assert summary["source_chain_frontier_statutes"] == {
        "effect_rows_not_admitted_by_replay_lens": ["ukpga/1954/21"],
    }
    assert summary["replay_lens_frontier_count"] == 1
    assert summary["replay_lens_frontier_statutes"] == ["ukpga/1954/21"]
    assert summary["non_manual_source_chain_frontier_count"] == 0


def test_source_chain_frontier_row_preserves_multiple_reasons(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        uk_broad_baseline,
        "score_one",
        lambda _statute_id: {
            "statute_id": "ukpga/1901/7",
            "score_status": "scored",
            "aligned": 91.7,
            "aligned_excluding_grounding_collateral": 91.7,
            "unaligned": 32.8,
            "n_replay": 22,
            "n_oracle": 24,
            "n_effects": 1,
            "n_ops": 0,
            "n_compile_rejections": 1,
            "compile_rejection_rule_counts": {
                "uk_effect_missing_structural_payload_rejected": 1,
            },
            "manual_frontier_status_counts": {"source_insufficient": 1},
        },
    )

    assert uk_broad_baseline.main(["--one", "ukpga/1901/7"]) == 0
    row = json.loads(capsys.readouterr().out)

    assert row["source_chain_frontier"] is True
    assert (
        row["source_chain_frontier_reason"]
        == "effect_rows_missing_structural_payload"
    )
    assert row["source_chain_frontier_reasons"] == [
        "effect_rows_missing_structural_payload",
        "manual_frontier_source_insufficient",
    ]


def test_run_driver_can_fail_on_active_unclassified_residuals(monkeypatch, capsys) -> None:
    def fake_run(*_args, **_kwargs):
        row = {
            "statute_id": "ukpga/1986/61",
            "score_status": "scored",
            "aligned": 50.9,
            "aligned_excluding_grounding_collateral": 50.9,
            "unaligned": 50.5,
            "n_replay": 289,
            "n_oracle": 568,
            "n_only_in_oracle": 279,
            "n_only_in_replayed": 0,
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(row), stderr="")

    monkeypatch.setattr(uk_broad_baseline.subprocess, "run", fake_run)

    assert (
        uk_broad_baseline.run_driver(
            ["ukpga/1986/61"],
            None,
            fail_on_active_unclassified_residuals=True,
        )
        == 1
    )
    assert "active_unclassified_residuals=1: ukpga/1986/61" in capsys.readouterr().out


def test_run_driver_writes_broad_baseline_report(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    def fake_run(*_args, **_kwargs):
        row = {
            "statute_id": "ukpga/1992/41",
            "score_status": "scored",
            "aligned": 98.7,
            "aligned_excluding_grounding_collateral": 98.7,
            "unaligned": 98.7,
            "n_replay": 10,
            "n_oracle": 10,
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(row), stderr="")

    monkeypatch.setattr(uk_broad_baseline.subprocess, "run", fake_run)
    snapshot_path = tmp_path / "snapshot.json"
    report_path = tmp_path / "report.json"

    assert (
        uk_broad_baseline.run_driver(
            ["ukpga/1992/41"],
            snapshot_path,
            report_path,
        )
        == 0
    )

    report = json.loads(report_path.read_text())
    assert report["report_kind"] == "uk_broad_baseline_agreement_report"
    assert report["filters"]["ids"] == ["ukpga/1992/41"]
    assert report["filters"]["snapshot_path"] == str(snapshot_path)
    assert report["summary"]["scored_count"] == 1
    assert report["rows"][0]["triage_bucket"] == "high_fidelity_after_grounding"
    assert "Wrote broad-baseline evidence report" in capsys.readouterr().out


def test_load_ids_file_accepts_newline_and_csv_forms(tmp_path) -> None:
    newline_path = tmp_path / "ids.txt"
    newline_path.write_text(
        "ukpga/1992/41\n\n# comment\nukpga/1986/61  # trailing note\n",
        encoding="utf-8",
    )
    csv_path = tmp_path / "ids.csv"
    csv_path.write_text(
        "statute_id,type\nukpga/1992/41,ukpga\nukpga/1986/61,ukpga\n",
        encoding="utf-8",
    )

    assert uk_broad_baseline._load_ids_file(newline_path) == [
        "ukpga/1992/41",
        "ukpga/1986/61",
    ]
    assert uk_broad_baseline._load_ids_file(csv_path) == [
        "ukpga/1992/41",
        "ukpga/1986/61",
    ]


def test_report_from_snapshot_recomputes_report_layer_annotations(
    tmp_path,
    capsys,
) -> None:
    snapshot_path = tmp_path / "snapshot.json"
    report_path = tmp_path / "report.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "ukpga/1992/41": {
                    "statute_id": "ukpga/1992/41",
                    "score_status": "scored",
                    "aligned": 98.7,
                    "aligned_excluding_grounding_collateral": 98.7,
                    "unaligned": 98.7,
                    "n_replay": 10,
                    "n_oracle": 10,
                    "triage_bucket": "stale_old_bucket",
                    "agreement_residual": {"family": "stale_old_family"},
                }
            }
        )
    )

    assert (
        uk_broad_baseline.run_report_from_snapshot(snapshot_path, report_path)
        == 0
    )

    report = json.loads(report_path.read_text())
    assert report["filters"]["ids"] == ["ukpga/1992/41"]
    assert report["filters"]["snapshot_path"] == str(snapshot_path)
    assert report["summary"]["scored_count"] == 1
    assert report["rows"][0]["triage_bucket"] == "high_fidelity_after_grounding"
    assert report["rows"][0]["agreement_residual"]["family"] == "agreement"
    assert "from snapshot" in capsys.readouterr().out


def test_report_from_snapshot_fail_flags_use_recomputed_summary(tmp_path) -> None:
    snapshot_path = tmp_path / "snapshot.json"
    report_path = tmp_path / "report.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "ukpga/1986/61": {
                    "statute_id": "ukpga/1986/61",
                    "score_status": "scored",
                    "aligned": 50.9,
                    "aligned_excluding_grounding_collateral": 50.9,
                    "unaligned": 50.5,
                    "n_replay": 289,
                    "n_oracle": 568,
                    "n_only_in_oracle": 279,
                    "n_only_in_replayed": 0,
                }
            }
        )
    )

    assert (
        uk_broad_baseline.run_report_from_snapshot(
            snapshot_path,
            report_path,
            fail_on_active_unclassified_residuals=True,
        )
        == 1
    )

    report = json.loads(report_path.read_text())
    assert report["summary"]["active_unclassified_residual_count"] == 1


def test_report_from_snapshot_can_fail_on_mutation_boundary_unexplained(
    tmp_path,
) -> None:
    snapshot_path = tmp_path / "snapshot.json"
    report_path = tmp_path / "report.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "ukpga/2000/8": {
                    "statute_id": "ukpga/2000/8",
                    "score_status": "scored",
                    "aligned": 99.0,
                    "aligned_excluding_grounding_collateral": 99.0,
                    "unaligned": 99.0,
                    "n_replay": 100,
                    "n_oracle": 100,
                    "n_mutation_boundary_unexplained_reports": 1,
                    "n_mutation_boundary_unexplained_paths": 2,
                }
            }
        )
    )

    assert (
        uk_broad_baseline.run_report_from_snapshot(
            snapshot_path,
            report_path,
            fail_on_mutation_boundary_unexplained=True,
        )
        == 1
    )

    report = json.loads(report_path.read_text())
    assert report["summary"]["mutation_boundary_unexplained_report_count"] == 1
    assert report["summary"]["mutation_boundary_unexplained_path_count"] == 2


def test_report_from_snapshot_can_fail_on_frontier_work_item_packet_gaps(
    tmp_path,
) -> None:
    snapshot_path = tmp_path / "snapshot.json"
    report_path = tmp_path / "report.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "ukpga/2008/17": {
                    "statute_id": "ukpga/2008/17",
                    "score_status": "scored",
                    "aligned": 86.0,
                    "aligned_excluding_grounding_collateral": 86.0,
                    "unaligned": 86.0,
                    "n_replay": 100,
                    "n_oracle": 110,
                    "manual_frontier_work_item_packet_ready_counts": {
                        "not_ready": 1,
                    },
                    "manual_frontier_work_item_packet_missing_field_counts": {
                        "execution_authorization": 1,
                    },
                }
            }
        )
    )

    assert (
        uk_broad_baseline.run_report_from_snapshot(
            snapshot_path,
            report_path,
            fail_on_frontier_work_item_packet_gaps=True,
        )
        == 1
    )

    report = json.loads(report_path.read_text())
    assert report["summary"]["manual_frontier_work_item_packet_ready_counts"] == {
        "not_ready": 1,
    }
    assert report["summary"][
        "manual_frontier_work_item_packet_missing_field_counts"
    ] == {
        "execution_authorization": 1,
        "target_resolution_coverage": 1,
    }
    assert report["summary"][
        "manual_frontier_work_item_packet_target_resolution_coverage_counts"
    ] == {"unproven": 1}


def test_report_from_snapshot_downgrades_stale_ready_frontier_packet(
    tmp_path,
) -> None:
    snapshot_path = tmp_path / "snapshot.json"
    report_path = tmp_path / "report.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "ukpga/2008/17": {
                    "statute_id": "ukpga/2008/17",
                    "score_status": "scored",
                    "aligned": 86.0,
                    "aligned_excluding_grounding_collateral": 86.0,
                    "unaligned": 86.0,
                    "n_replay": 100,
                    "n_oracle": 110,
                    "manual_frontier_work_item_packet_ready_counts": {
                        "ready": 1,
                    },
                    "manual_frontier_work_item_packet_missing_field_counts": {},
                }
            }
        )
    )

    assert (
        uk_broad_baseline.run_report_from_snapshot(
            snapshot_path,
            report_path,
            fail_on_frontier_work_item_packet_gaps=True,
        )
        == 1
    )

    report = json.loads(report_path.read_text())
    assert report["summary"]["manual_frontier_work_item_packet_ready_counts"] == {
        "not_ready": 1,
    }
    assert report["summary"][
        "manual_frontier_work_item_packet_missing_field_counts"
    ] == {"target_resolution_coverage": 1}
    assert report["summary"][
        "manual_frontier_work_item_packet_target_resolution_coverage_counts"
    ] == {"unproven": 1}


def test_report_from_snapshot_accepts_fresh_target_resolution_packet_schema(
    tmp_path,
) -> None:
    snapshot_path = tmp_path / "snapshot.json"
    report_path = tmp_path / "report.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "ukpga/2008/17": {
                    "statute_id": "ukpga/2008/17",
                    "score_status": "scored",
                    "aligned": 86.0,
                    "aligned_excluding_grounding_collateral": 86.0,
                    "unaligned": 86.0,
                    "n_replay": 100,
                    "n_oracle": 110,
                    "manual_frontier_work_item_packet_ready_counts": {
                        "ready": 1,
                    },
                    "manual_frontier_work_item_packet_missing_field_counts": {},
                    "manual_frontier_work_item_packet_target_resolution_coverage_counts": {
                        "present": 1,
                    },
                }
            }
        )
    )

    assert (
        uk_broad_baseline.run_report_from_snapshot(
            snapshot_path,
            report_path,
            fail_on_frontier_work_item_packet_gaps=True,
        )
        == 0
    )

    report = json.loads(report_path.read_text())
    assert report["summary"]["manual_frontier_work_item_packet_ready_counts"] == {
        "ready": 1,
    }
    assert report["summary"][
        "manual_frontier_work_item_packet_missing_field_counts"
    ] == {}
    assert report["summary"][
        "manual_frontier_work_item_packet_target_resolution_coverage_counts"
    ] == {"present": 1}


def test_report_from_snapshot_can_fail_on_frontier_candidate_set_gaps(
    tmp_path,
) -> None:
    snapshot_path = tmp_path / "snapshot.json"
    report_path = tmp_path / "report.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "ukpga/2008/17": {
                    "statute_id": "ukpga/2008/17",
                    "score_status": "scored",
                    "aligned": 86.0,
                    "aligned_excluding_grounding_collateral": 86.0,
                    "unaligned": 86.0,
                    "n_replay": 100,
                    "n_oracle": 110,
                    "manual_frontier_work_item_candidate_set_status_counts": {
                        "partial": 1,
                    },
                }
            }
        )
    )

    assert (
        uk_broad_baseline.run_report_from_snapshot(
            snapshot_path,
            report_path,
            fail_on_frontier_candidate_set_gaps=True,
        )
        == 1
    )

    report = json.loads(report_path.read_text())
    assert report["summary"][
        "manual_frontier_work_item_candidate_set_status_counts"
    ] == {"partial": 1}


def test_report_from_snapshot_can_fail_on_frontier_target_resolution_gaps(
    tmp_path,
) -> None:
    snapshot_path = tmp_path / "snapshot.json"
    report_path = tmp_path / "report.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "ukpga/2008/17": {
                    "statute_id": "ukpga/2008/17",
                    "score_status": "scored",
                    "aligned": 86.0,
                    "aligned_excluding_grounding_collateral": 86.0,
                    "unaligned": 86.0,
                    "n_replay": 100,
                    "n_oracle": 110,
                    "manual_frontier_work_item_target_resolution_status_counts": {
                        "unresolved": 1,
                    },
                }
            }
        )
    )

    assert (
        uk_broad_baseline.run_report_from_snapshot(
            snapshot_path,
            report_path,
            fail_on_frontier_target_resolution_gaps=True,
        )
        == 1
    )

    report = json.loads(report_path.read_text())
    assert report["summary"][
        "manual_frontier_work_item_target_resolution_status_counts"
    ] == {"unresolved": 1}


def test_report_from_snapshot_allows_non_textual_target_resolution_exemptions(
    tmp_path,
) -> None:
    snapshot_path = tmp_path / "snapshot.json"
    report_path = tmp_path / "report.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "ukpga/2008/17": {
                    "statute_id": "ukpga/2008/17",
                    "score_status": "scored",
                    "aligned": 86.0,
                    "aligned_excluding_grounding_collateral": 86.0,
                    "unaligned": 86.0,
                    "n_replay": 100,
                    "n_oracle": 110,
                    "manual_frontier_work_item_candidate_set_status_counts": {
                        "unavailable": 1,
                    },
                    "manual_frontier_work_item_candidate_set_gap_counts": {},
                    "manual_frontier_work_item_candidate_set_exempt_counts": {
                        "unavailable:uk_manual_frontier_non_textual_or_out_of_scope": 1,
                    },
                    "manual_frontier_work_item_target_resolution_status_counts": {
                        "unresolved": 1,
                    },
                    "manual_frontier_work_item_target_resolution_gap_counts": {},
                    "manual_frontier_work_item_target_resolution_exempt_counts": {
                        "unresolved:uk_manual_frontier_non_textual_or_out_of_scope": 1,
                    },
                }
            }
        )
    )

    assert (
        uk_broad_baseline.run_report_from_snapshot(
            snapshot_path,
            report_path,
            fail_on_frontier_candidate_set_gaps=True,
            fail_on_frontier_target_resolution_gaps=True,
        )
        == 0
    )

    report = json.loads(report_path.read_text())
    assert report["summary"][
        "manual_frontier_work_item_candidate_set_exempt_counts"
    ] == {"unavailable:uk_manual_frontier_non_textual_or_out_of_scope": 1}
    assert report["summary"][
        "manual_frontier_work_item_target_resolution_exempt_counts"
    ] == {"unresolved:uk_manual_frontier_non_textual_or_out_of_scope": 1}


def test_report_from_snapshot_allows_ambiguous_frontier_target_resolution(
    tmp_path,
) -> None:
    snapshot_path = tmp_path / "snapshot.json"
    report_path = tmp_path / "report.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "ukpga/2008/17": {
                    "statute_id": "ukpga/2008/17",
                    "score_status": "scored",
                    "aligned": 86.0,
                    "aligned_excluding_grounding_collateral": 86.0,
                    "unaligned": 86.0,
                    "n_replay": 100,
                    "n_oracle": 110,
                    "manual_frontier_work_item_target_resolution_status_counts": {
                        "ambiguous": 1,
                    },
                }
            }
        )
    )

    assert (
        uk_broad_baseline.run_report_from_snapshot(
            snapshot_path,
            report_path,
            fail_on_frontier_target_resolution_gaps=True,
        )
        == 0
    )

    report = json.loads(report_path.read_text())
    assert report["summary"][
        "manual_frontier_work_item_target_resolution_status_counts"
    ] == {"ambiguous": 1}


def test_report_from_snapshot_fails_closed_on_unproved_target_resolution_status(
    tmp_path,
) -> None:
    snapshot_path = tmp_path / "snapshot.json"
    report_path = tmp_path / "report.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "ukpga/2008/17": {
                    "statute_id": "ukpga/2008/17",
                    "score_status": "scored",
                    "aligned": 86.0,
                    "aligned_excluding_grounding_collateral": 86.0,
                    "unaligned": 86.0,
                    "n_replay": 100,
                    "n_oracle": 110,
                    "manual_frontier_work_item_packet_target_resolution_coverage_counts": {
                        "present": 1,
                    },
                }
            }
        )
    )

    assert (
        uk_broad_baseline.run_report_from_snapshot(
            snapshot_path,
            report_path,
            fail_on_frontier_target_resolution_gaps=True,
        )
        == 1
    )

    report = json.loads(report_path.read_text())
    assert report["summary"][
        "manual_frontier_work_item_target_resolution_status_counts"
    ] == {"unproven": 1}


def test_report_from_snapshot_omits_zero_target_resolution_status_counts(
    tmp_path,
) -> None:
    snapshot_path = tmp_path / "snapshot.json"
    report_path = tmp_path / "report.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "ukpga/2008/17": {
                    "statute_id": "ukpga/2008/17",
                    "score_status": "scored",
                    "aligned": 86.0,
                    "aligned_excluding_grounding_collateral": 86.0,
                    "unaligned": 86.0,
                    "n_replay": 100,
                    "n_oracle": 110,
                }
            }
        )
    )

    assert uk_broad_baseline.run_report_from_snapshot(snapshot_path, report_path) == 0

    report = json.loads(report_path.read_text())
    assert report["summary"][
        "manual_frontier_work_item_target_resolution_status_counts"
    ] == {}


def test_report_from_snapshot_exposes_completion_gate_failures(
    tmp_path,
) -> None:
    snapshot_path = tmp_path / "snapshot.json"
    report_path = tmp_path / "report.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "ukpga/2008/17": {
                    "statute_id": "ukpga/2008/17",
                    "score_status": "scored",
                    "aligned": 86.0,
                    "aligned_excluding_grounding_collateral": 86.0,
                    "unaligned": 86.0,
                    "n_replay": 100,
                    "n_oracle": 110,
                    "manual_frontier_template_gap_rule_counts": {
                        "uk_manual_frontier_parser_or_extraction_candidate": 1,
                    },
                    "manual_frontier_work_item_packet_ready_counts": {
                        "not_ready": 1,
                    },
                    "manual_frontier_work_item_candidate_set_status_counts": {
                        "partial": 1,
                    },
                    "manual_frontier_work_item_source_witness_digest_coverage_counts": {
                        "missing_digest": 1,
                    },
                }
            }
        )
    )

    assert (
        uk_broad_baseline.run_report_from_snapshot(
            snapshot_path,
            report_path,
            fail_on_completion_gaps=True,
        )
        == 1
    )

    summary = json.loads(report_path.read_text())["summary"]
    assert summary["completion_gate_clean"] is False
    assert summary["completion_gate_failure_counts"] == {
        "frontier_candidate_set_gaps": 1,
        "frontier_source_witness_gaps": 1,
        "frontier_target_resolution_gaps": 1,
        "frontier_work_item_packet_gaps": 2,
        "manual_frontier_template_gaps": 1,
    }


def test_report_from_snapshot_can_fail_on_frontier_source_witness_gaps(
    tmp_path,
) -> None:
    snapshot_path = tmp_path / "snapshot.json"
    report_path = tmp_path / "report.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "ukpga/2008/17": {
                    "statute_id": "ukpga/2008/17",
                    "score_status": "scored",
                    "aligned": 86.0,
                    "aligned_excluding_grounding_collateral": 86.0,
                    "unaligned": 86.0,
                    "n_replay": 100,
                    "n_oracle": 110,
                    "manual_frontier_work_item_source_witness_role_counts": {
                        "effect_feed_row": 1,
                    },
                    "manual_frontier_work_item_source_witness_digest_coverage_counts": {
                        "missing_digest": 1,
                    },
                }
            }
        )
    )

    assert (
        uk_broad_baseline.run_report_from_snapshot(
            snapshot_path,
            report_path,
            fail_on_frontier_source_witness_gaps=True,
        )
        == 1
    )

    report = json.loads(report_path.read_text())
    assert report["summary"][
        "manual_frontier_work_item_source_witness_role_counts"
    ] == {"effect_feed_row": 1}
    assert report["summary"][
        "manual_frontier_work_item_source_witness_digest_coverage_counts"
    ] == {"missing_digest": 1}


def test_report_from_snapshot_can_fail_on_source_frontier_work_item_gaps(
    tmp_path,
) -> None:
    snapshot_path = tmp_path / "snapshot.json"
    report_path = tmp_path / "report.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "ukpga/1850/102": {
                    "statute_id": "ukpga/1850/102",
                    "score_status": "source_frontier",
                    "source_frontier_reason": "base_and_oracle_metadata_only",
                    "base_source_status": "metadata_only",
                    "oracle_source_status": "metadata_only",
                }
            }
        )
    )

    assert (
        uk_broad_baseline.run_report_from_snapshot(
            snapshot_path,
            report_path,
            fail_on_source_frontier_work_item_gaps=True,
        )
        == 1
    )

    summary = json.loads(report_path.read_text())["summary"]
    assert summary["source_frontier_work_item_family_counts"] == {
        "uk_source_frontier_base_and_oracle_metadata_only": 1,
    }
    assert summary["source_frontier_work_item_authorization_status_counts"] == {
        "source_footing_gap": 1,
    }
    assert summary["source_frontier_source_witness_digest_coverage_counts"] == {
        "base:stale_snapshot_missing_source_witness": 1,
        "oracle:stale_snapshot_missing_source_witness": 1,
    }
    assert summary["completion_gate_failure_counts"] == {
        "source_frontier_work_item_gaps": 2,
    }


def test_report_from_snapshot_allows_digest_backed_source_frontier_work_item(
    tmp_path,
) -> None:
    snapshot_path = tmp_path / "snapshot.json"
    report_path = tmp_path / "report.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "ukpga/1850/102": {
                    "statute_id": "ukpga/1850/102",
                    "score_status": "source_frontier",
                    "source_frontier_reason": "base_and_oracle_metadata_only",
                    "base_source_status": "metadata_only",
                    "oracle_source_status": "metadata_only",
                    "base_source_witness": {
                        "source_role": "uk_broad_base_source",
                        "digest": "base-digest",
                        "preview_digest": "base-preview-digest",
                    },
                    "oracle_source_witness": {
                        "source_role": "uk_broad_oracle_source",
                        "digest": "oracle-digest",
                        "preview_digest": "oracle-preview-digest",
                    },
                }
            }
        )
    )

    assert (
        uk_broad_baseline.run_report_from_snapshot(
            snapshot_path,
            report_path,
            fail_on_completion_gaps=True,
            fail_on_source_frontier_work_item_gaps=True,
        )
        == 0
    )

    summary = json.loads(report_path.read_text())["summary"]
    assert summary["source_frontier_work_item_family_counts"] == {
        "uk_source_frontier_base_and_oracle_metadata_only": 1,
    }
    assert summary["source_frontier_source_witness_digest_coverage_counts"] == {
        "base:artifact_and_preview_digest": 1,
        "oracle:artifact_and_preview_digest": 1,
    }
    assert summary["completion_gate_clean"] is True
    assert summary["completion_gate_failure_counts"] == {}


def test_report_from_snapshot_accepts_evidence_surface_report_rows(
    tmp_path,
) -> None:
    report_input_path = tmp_path / "input.report.json"
    report_path = tmp_path / "report.json"
    row = {
        "statute_id": "ukpga/1850/102",
        "score_status": "source_frontier",
        "source_frontier_reason": "base_and_oracle_metadata_only",
        "base_source_status": "metadata_only",
        "oracle_source_status": "metadata_only",
        "base_source_witness": {
            "source_role": "uk_broad_base_source",
            "digest": "base-digest",
            "preview_digest": "base-preview-digest",
        },
        "oracle_source_witness": {
            "source_role": "uk_broad_oracle_source",
            "digest": "oracle-digest",
            "preview_digest": "oracle-preview-digest",
        },
    }
    report_input_path.write_text(
        json.dumps(
            {
                "schema": "lawvm.evidence_surface_report.v1",
                "jurisdiction": "uk",
                "report_kind": "uk_broad_baseline",
                "summary": {},
                "rows": [row],
                "rows_truncated": False,
            }
        )
    )

    assert (
        uk_broad_baseline.run_report_from_snapshot(
            report_input_path,
            report_path,
            fail_on_completion_gaps=True,
            fail_on_source_frontier_work_item_gaps=True,
        )
        == 0
    )

    summary = json.loads(report_path.read_text())["summary"]
    assert summary["source_frontier_source_witness_digest_coverage_counts"] == {
        "base:artifact_and_preview_digest": 1,
        "oracle:artifact_and_preview_digest": 1,
    }
    assert summary["completion_gate_clean"] is True
    assert summary["completion_gate_failure_counts"] == {}


def test_main_report_from_snapshot_requires_out_report(tmp_path, capsys) -> None:
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text("{}")

    assert (
        uk_broad_baseline.main(["--report-from-snapshot", str(snapshot_path)])
        == 2
    )

    assert "--report-from-snapshot requires --out-report" in capsys.readouterr().err


def test_run_driver_parallel_preserves_report_input_order(
    monkeypatch,
    tmp_path,
) -> None:
    def fake_run(args, **_kwargs):
        statute_id = args[-1]
        if statute_id == "ukpga/2000/1":
            time.sleep(0.02)
        row = {
            "statute_id": statute_id,
            "score_status": "scored",
            "aligned": 100.0,
            "aligned_excluding_grounding_collateral": 100.0,
            "unaligned": 100.0,
            "n_replay": 10,
            "n_oracle": 10,
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(row), stderr="")

    monkeypatch.setattr(uk_broad_baseline.subprocess, "run", fake_run)
    report_path = tmp_path / "report.json"

    assert (
        uk_broad_baseline.run_driver(
            ["ukpga/2000/1", "ukpga/2000/2"],
            None,
            report_path,
            parallel=2,
        )
        == 0
    )

    report = json.loads(report_path.read_text())
    assert [row["statute_id"] for row in report["rows"]] == [
        "ukpga/2000/1",
        "ukpga/2000/2",
    ]


def test_run_driver_fail_flag_accepts_manual_frontier_residuals(monkeypatch, capsys) -> None:
    def fake_run(*_args, **_kwargs):
        row = {
            "statute_id": "ukpga/1990/8",
            "score_status": "scored",
            "aligned": 83.81,
            "aligned_excluding_grounding_collateral": 83.81,
            "unaligned": 74.4,
            "n_replay": 6906,
            "n_oracle": 8240,
            "n_only_in_oracle": 1334,
            "n_only_in_replayed": 0,
            "manual_frontier_status_counts": {
                "manual_compile_candidate": 46,
            },
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(row), stderr="")

    monkeypatch.setattr(uk_broad_baseline.subprocess, "run", fake_run)

    assert (
        uk_broad_baseline.run_driver(
            ["ukpga/1990/8"],
            None,
            fail_on_active_unclassified_residuals=True,
        )
        == 0
    )
    assert "active_unclassified_residuals=0" in capsys.readouterr().out


def test_run_driver_can_fail_on_deterministic_frontend_candidates(
    monkeypatch,
    capsys,
) -> None:
    def fake_run(*_args, **_kwargs):
        row = {
            "statute_id": "ukpga/2008/17",
            "score_status": "scored",
            "aligned": 99.5,
            "aligned_excluding_grounding_collateral": 99.5,
            "unaligned": 88.8,
            "n_replay": 4930,
            "n_oracle": 4955,
            "manual_frontier_status_counts": {
                "deterministic_frontend_candidate": 1,
                "manual_compile_candidate": 16,
            },
            "manual_frontier_deterministic_candidate_rule_counts": {
                "uk_manual_frontier_table_entry_candidate": 1,
            },
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(row), stderr="")

    monkeypatch.setattr(uk_broad_baseline.subprocess, "run", fake_run)

    assert (
        uk_broad_baseline.run_driver(
            ["ukpga/2008/17"],
            None,
            fail_on_deterministic_frontend_candidates=True,
        )
        == 1
    )
    out = capsys.readouterr().out
    assert "deterministic_frontend_candidates=1: ukpga/2008/17" in out
    assert (
        "deterministic_frontend_candidate_rule_counts: "
        "uk_manual_frontier_table_entry_candidate=1"
    ) in out


def test_run_driver_can_fail_on_mutation_boundary_unexplained(
    monkeypatch,
    capsys,
) -> None:
    def fake_run(*_args, **_kwargs):
        row = {
            "statute_id": "ukpga/2000/8",
            "score_status": "scored",
            "aligned": 99.0,
            "aligned_excluding_grounding_collateral": 99.0,
            "unaligned": 99.0,
            "n_replay": 100,
            "n_oracle": 100,
            "n_mutation_events": 3,
            "n_mutation_boundary_reports": 3,
            "n_mutation_boundary_unexplained_reports": 1,
            "n_mutation_boundary_unexplained_paths": 2,
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(row), stderr="")

    monkeypatch.setattr(uk_broad_baseline.subprocess, "run", fake_run)

    assert (
        uk_broad_baseline.run_driver(
            ["ukpga/2000/8"],
            None,
            fail_on_mutation_boundary_unexplained=True,
        )
        == 1
    )
    out = capsys.readouterr().out
    assert "mutation_boundary: events=3 reports=3 unexplained_reports=1" in out
    assert "unexplained_paths=2" in out


def test_run_driver_can_fail_on_manual_frontier_template_gaps(monkeypatch, capsys) -> None:
    def fake_run(*_args, **_kwargs):
        row = {
            "statute_id": "ukpga/2008/17",
            "score_status": "scored",
            "aligned": 86.0,
            "aligned_excluding_grounding_collateral": 86.0,
            "unaligned": 86.0,
            "n_replay": 100,
            "n_oracle": 110,
            "manual_frontier_status_counts": {
                "deterministic_frontend_candidate": 2,
            },
            "manual_frontier_template_status_counts": {
                "not_available": 2,
            },
            "manual_frontier_template_gap_rule_counts": {
                "uk_manual_frontier_parser_or_extraction_candidate": 2,
            },
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(row), stderr="")

    monkeypatch.setattr(uk_broad_baseline.subprocess, "run", fake_run)

    assert (
        uk_broad_baseline.run_driver(
            ["ukpga/2008/17"],
            None,
            fail_on_manual_frontier_template_gaps=True,
        )
        == 1
    )
    out = capsys.readouterr().out
    assert "manual_frontier_template_gaps: " in out
    assert "uk_manual_frontier_parser_or_extraction_candidate=2" in out


def test_run_driver_can_fail_on_frontier_work_item_gaps(monkeypatch, capsys) -> None:
    def fake_run(*_args, **_kwargs):
        row = {
            "statute_id": "ukpga/2008/17",
            "score_status": "scored",
            "aligned": 86.0,
            "aligned_excluding_grounding_collateral": 86.0,
            "unaligned": 86.0,
            "n_replay": 100,
            "n_oracle": 110,
            "manual_frontier_work_item_missing_candidate_operation_family_count": 2,
            "manual_frontier_work_item_missing_required_validator_checks_count": 3,
            "manual_frontier_work_item_source_witness_role_counts": {
                "effect_feed_row": 2,
            },
            "manual_frontier_work_item_source_witness_digest_coverage_counts": {
                "preview_digest": 2,
            },
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(row), stderr="")

    monkeypatch.setattr(uk_broad_baseline.subprocess, "run", fake_run)

    assert (
        uk_broad_baseline.run_driver(
            ["ukpga/2008/17"],
            None,
            fail_on_frontier_work_item_gaps=True,
        )
        == 1
    )
    out = capsys.readouterr().out
    assert "manual_frontier_work_item_missing_completeness_counts: " in out
    assert "candidate_operation_family=2" in out
    assert "required_validator_checks=3" in out
    assert "manual_frontier_work_item_source_witness_role_counts: effect_feed_row=2" in out
    assert (
        "manual_frontier_work_item_source_witness_digest_coverage_counts: "
        "preview_digest=2"
    ) in out


def test_run_driver_can_fail_on_frontier_work_item_packet_gaps(
    monkeypatch,
    capsys,
) -> None:
    def fake_run(*_args, **_kwargs):
        row = {
            "statute_id": "ukpga/2008/17",
            "score_status": "scored",
            "aligned": 86.0,
            "aligned_excluding_grounding_collateral": 86.0,
            "unaligned": 86.0,
            "n_replay": 100,
            "n_oracle": 110,
            "manual_frontier_work_item_packet_ready_counts": {
                "not_ready": 1,
            },
            "manual_frontier_work_item_packet_missing_field_counts": {
                "execution_authorization": 1,
            },
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(row), stderr="")

    monkeypatch.setattr(uk_broad_baseline.subprocess, "run", fake_run)

    assert (
        uk_broad_baseline.run_driver(
            ["ukpga/2008/17"],
            None,
            fail_on_frontier_work_item_packet_gaps=True,
        )
        == 1
    )
    out = capsys.readouterr().out
    assert "manual_frontier_work_item_packet_ready_counts: not_ready=1" in out
    assert (
        "manual_frontier_work_item_packet_missing_field_counts: "
        "execution_authorization=1"
    ) in out


def test_run_driver_can_fail_on_frontier_candidate_set_gaps(
    monkeypatch,
    capsys,
) -> None:
    def fake_run(*_args, **_kwargs):
        row = {
            "statute_id": "ukpga/2008/17",
            "score_status": "scored",
            "aligned": 86.0,
            "aligned_excluding_grounding_collateral": 86.0,
            "unaligned": 86.0,
            "n_replay": 100,
            "n_oracle": 110,
            "manual_frontier_work_item_candidate_set_status_counts": {
                "partial": 1,
            },
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(row), stderr="")

    monkeypatch.setattr(uk_broad_baseline.subprocess, "run", fake_run)

    assert (
        uk_broad_baseline.run_driver(
            ["ukpga/2008/17"],
            None,
            fail_on_frontier_candidate_set_gaps=True,
        )
        == 1
    )
    out = capsys.readouterr().out
    assert "manual_frontier_work_item_candidate_set_status_counts: partial=1" in out


def test_run_driver_prints_frontier_proof_obligation_counts(
    monkeypatch,
    capsys,
) -> None:
    def fake_run(*_args, **_kwargs):
        row = {
            "statute_id": "ukpga/2008/17",
            "score_status": "scored",
            "aligned": 86.0,
            "aligned_excluding_grounding_collateral": 86.0,
            "unaligned": 86.0,
            "n_replay": 100,
            "n_oracle": 110,
            "manual_frontier_work_item_proof_obligation_status_counts": {
                "blocked": 1,
            },
            "manual_frontier_work_item_proof_obligation_proved_counts": {
                "source_list_membership": 1,
            },
            "manual_frontier_work_item_proof_obligation_blocker_counts": {
                "mutation_boundary_proof": 1,
            },
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(row), stderr="")

    monkeypatch.setattr(uk_broad_baseline.subprocess, "run", fake_run)

    assert uk_broad_baseline.run_driver(["ukpga/2008/17"], None) == 0
    out = capsys.readouterr().out
    assert "manual_frontier_work_item_proof_obligation_status_counts: blocked=1" in out
    assert (
        "manual_frontier_work_item_proof_obligation_proved_counts: "
        "source_list_membership=1"
    ) in out
    assert (
        "manual_frontier_work_item_proof_obligation_blocker_counts: "
        "mutation_boundary_proof=1"
    ) in out


def test_run_driver_can_fail_on_frontier_target_resolution_gaps(
    monkeypatch,
    capsys,
) -> None:
    def fake_run(*_args, **_kwargs):
        row = {
            "statute_id": "ukpga/2008/17",
            "score_status": "scored",
            "aligned": 86.0,
            "aligned_excluding_grounding_collateral": 86.0,
            "unaligned": 86.0,
            "n_replay": 100,
            "n_oracle": 110,
            "manual_frontier_work_item_target_resolution_status_counts": {
                "unresolved": 1,
            },
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(row), stderr="")

    monkeypatch.setattr(uk_broad_baseline.subprocess, "run", fake_run)

    assert (
        uk_broad_baseline.run_driver(
            ["ukpga/2008/17"],
            None,
            fail_on_frontier_target_resolution_gaps=True,
        )
        == 1
    )
    out = capsys.readouterr().out
    assert (
        "manual_frontier_work_item_target_resolution_status_counts: unresolved=1"
        in out
    )


def test_run_driver_can_fail_on_completion_gaps(
    monkeypatch,
    capsys,
) -> None:
    def fake_run(*_args, **_kwargs):
        row = {
            "statute_id": "ukpga/2008/17",
            "score_status": "scored",
            "aligned": 86.0,
            "aligned_excluding_grounding_collateral": 86.0,
            "unaligned": 86.0,
            "n_replay": 100,
            "n_oracle": 110,
            "manual_frontier_work_item_candidate_set_status_counts": {
                "partial": 1,
            },
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(row), stderr="")

    monkeypatch.setattr(uk_broad_baseline.subprocess, "run", fake_run)

    assert (
        uk_broad_baseline.run_driver(
            ["ukpga/2008/17"],
            None,
            fail_on_completion_gaps=True,
        )
        == 1
    )
    out = capsys.readouterr().out
    assert "completion_gate_failures: frontier_candidate_set_gaps=1" in out


def test_run_driver_can_fail_on_frontier_source_witness_gaps(
    monkeypatch,
    capsys,
) -> None:
    def fake_run(*_args, **_kwargs):
        row = {
            "statute_id": "ukpga/2008/17",
            "score_status": "scored",
            "aligned": 86.0,
            "aligned_excluding_grounding_collateral": 86.0,
            "unaligned": 86.0,
            "n_replay": 100,
            "n_oracle": 110,
            "manual_frontier_work_item_source_witness_role_counts": {
                "effect_feed_row": 1,
            },
            "manual_frontier_work_item_source_witness_digest_coverage_counts": {
                "missing_digest": 1,
            },
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(row), stderr="")

    monkeypatch.setattr(uk_broad_baseline.subprocess, "run", fake_run)

    assert (
        uk_broad_baseline.run_driver(
            ["ukpga/2008/17"],
            None,
            fail_on_frontier_source_witness_gaps=True,
        )
        == 1
    )
    out = capsys.readouterr().out
    assert "manual_frontier_work_item_source_witness_role_counts: effect_feed_row=1" in out
    assert (
        "manual_frontier_work_item_source_witness_digest_coverage_counts: "
        "missing_digest=1"
    ) in out


def test_run_driver_can_fail_on_source_frontier_work_item_gaps(
    monkeypatch,
    capsys,
) -> None:
    def fake_run(*_args, **_kwargs):
        row = {
            "statute_id": "ukpga/1850/102",
            "score_status": "source_frontier",
            "source_frontier_reason": "base_and_oracle_metadata_only",
            "base_source_status": "metadata_only",
            "oracle_source_status": "metadata_only",
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(row), stderr="")

    monkeypatch.setattr(uk_broad_baseline.subprocess, "run", fake_run)

    assert (
        uk_broad_baseline.run_driver(
            ["ukpga/1850/102"],
            None,
            fail_on_source_frontier_work_item_gaps=True,
        )
        == 1
    )
    out = capsys.readouterr().out
    assert (
        "source_frontier_work_item_family_counts: "
        "uk_source_frontier_base_and_oracle_metadata_only=1"
    ) in out
    assert (
        "source_frontier_source_witness_digest_coverage_counts: "
        "base:stale_snapshot_missing_source_witness=1, "
        "oracle:stale_snapshot_missing_source_witness=1"
    ) in out


def test_run_driver_can_fail_on_non_manual_source_chain_frontier(
    monkeypatch,
    capsys,
) -> None:
    def fake_run(*_args, **_kwargs):
        row = {
            "statute_id": "uksi/2012/1206",
            "score_status": "scored",
            "aligned": 88.6,
            "aligned_excluding_grounding_collateral": 88.6,
            "unaligned": 88.6,
            "n_replay": 31,
            "n_oracle": 35,
            "n_ops": 0,
            "n_effects": 0,
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(row), stderr="")

    monkeypatch.setattr(uk_broad_baseline.subprocess, "run", fake_run)

    assert (
        uk_broad_baseline.run_driver(
            ["uksi/2012/1206"],
            None,
            fail_on_non_manual_source_chain_frontier=True,
        )
        == 1
    )
    out = capsys.readouterr().out
    assert "source_chain_frontier[effect_rows_absent_or_unpublished]: uksi/2012/1206" in out
    assert "non_manual_source_chain_frontier=1: uksi/2012/1206" in out


def test_run_driver_non_manual_source_chain_flag_allows_empty_effect_feed(
    monkeypatch,
    capsys,
) -> None:
    def fake_run(*_args, **_kwargs):
        row = {
            "statute_id": "uksi/2012/1206",
            "score_status": "scored",
            "aligned": 88.6,
            "aligned_excluding_grounding_collateral": 88.6,
            "unaligned": 88.6,
            "n_replay": 31,
            "n_oracle": 35,
            "n_ops": 0,
            "n_effects": 0,
            "compile_rejection_rule_counts": {
                "uk_effect_feed_empty_recorded": 1,
            },
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(row), stderr="")

    monkeypatch.setattr(uk_broad_baseline.subprocess, "run", fake_run)

    assert (
        uk_broad_baseline.run_driver(
            ["uksi/2012/1206"],
            None,
            fail_on_non_manual_source_chain_frontier=True,
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "source_chain_frontier[effect_feed_empty]: uksi/2012/1206" in out
    assert "non_manual_source_chain_frontier=0" in out
    assert "empty_effect_feed_frontier=1: uksi/2012/1206" in out


def test_run_driver_non_manual_source_chain_flag_allows_source_or_oracle_pathology(
    monkeypatch,
    capsys,
) -> None:
    def fake_run(*_args, **_kwargs):
        row = {
            "statute_id": "ukpga/1945/9",
            "score_status": "source_frontier",
            "source_frontier_reason": "base_too_small",
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(row), stderr="")

    monkeypatch.setattr(uk_broad_baseline.subprocess, "run", fake_run)

    assert (
        uk_broad_baseline.run_driver(
            ["ukpga/1945/9"],
            None,
            fail_on_non_manual_source_chain_frontier=True,
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "source_chain_frontier[base_too_small]: ukpga/1945/9" in out
    assert "non_manual_source_chain_frontier=0" in out
    assert "source_or_oracle_pathology_frontier=1: ukpga/1945/9" in out
    assert "source_or_oracle_pathology_frontier_reasons: base_too_small=1" in out
    assert "source_or_oracle_pathology_frontier[base_too_small]: ukpga/1945/9" in out


def test_run_driver_non_manual_source_chain_flag_allows_manual_source_insufficient(
    monkeypatch,
    capsys,
) -> None:
    def fake_run(*_args, **_kwargs):
        row = {
            "statute_id": "ukpga/1990/8",
            "score_status": "scored",
            "aligned": 83.8,
            "aligned_excluding_grounding_collateral": 83.8,
            "unaligned": 74.4,
            "n_replay": 6906,
            "n_oracle": 8240,
            "manual_frontier_status_counts": {
                "source_insufficient": 2,
            },
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(row), stderr="")

    monkeypatch.setattr(uk_broad_baseline.subprocess, "run", fake_run)

    assert (
        uk_broad_baseline.run_driver(
            ["ukpga/1990/8"],
            None,
            fail_on_non_manual_source_chain_frontier=True,
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "source_chain_frontier[manual_frontier_source_insufficient]: ukpga/1990/8" in out
    assert "non_manual_source_chain_frontier=0" in out


def test_run_driver_non_manual_source_chain_flag_allows_text_patch_chain_gap(
    monkeypatch,
    capsys,
) -> None:
    def fake_run(*_args, **_kwargs):
        row = {
            "statute_id": "ukpga/1954/21",
            "score_status": "scored",
            "aligned": 70.0,
            "aligned_excluding_grounding_collateral": 70.0,
            "unaligned": 70.0,
            "n_replay": 28,
            "n_oracle": 40,
            "n_only_in_oracle": 12,
            "n_only_in_replayed": 0,
            "manual_frontier_rule_counts": {
                "uk_manual_frontier_text_patch_preimage_chain_gap": 1,
            },
            "manual_frontier_work_item_candidate_operation_family_counts": {
                "source_chain_text_patch": 1,
            },
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(row), stderr="")

    monkeypatch.setattr(uk_broad_baseline.subprocess, "run", fake_run)

    assert (
        uk_broad_baseline.run_driver(
            ["ukpga/1954/21"],
            None,
            fail_on_active_unclassified_residuals=True,
            fail_on_non_manual_source_chain_frontier=True,
        )
        == 0
    )
    out = capsys.readouterr().out
    assert (
        "source_chain_frontier[manual_frontier_source_chain_text_patch_gap]: "
        "ukpga/1954/21"
    ) in out
    assert "active_unclassified_residuals=0" in out
    assert "non_manual_source_chain_frontier=0" in out
