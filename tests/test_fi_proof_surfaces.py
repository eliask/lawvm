import ast
import hashlib
from datetime import date
from pathlib import Path

from lawvm.core.compile_result import SourcePathology
from lawvm.core.candidate_set_certificate import CANDIDATE_SET_COMPLETE, CandidateSetCertificate
from lawvm.core.manual_claims.kind_registry import list_registered_kinds
from lawvm.core.mutation_accounting import MutationInvariantReport
from lawvm.core.source_witness import source_witness_digest_coverage
import lawvm.finland.claim_kinds  # noqa: F401  # registers fi.v1.* claim kinds
from lawvm.finland.he_branch_parser import (
    BranchParseRecovery,
    BranchProposedOp,
    BranchTargetResolution,
    BranchTargetResolutionFinding,
    HEParsedBranch,
    HEParseStatus,
)
from lawvm.finland.proof_surfaces import (
    consolidated_artifact_source_witness,
    finland_bench_run_evidence_surface,
    finland_corrigendum_manual_template_evidence_surface,
    finland_corrigendum_manual_template_frontier_item,
    finland_corrigendum_open_manual_evidence_surface,
    finland_corrigendum_overview_evidence_surface,
    finland_corrigendum_provenance_evidence_surface,
    finland_corrigendum_unsupported_patch_evidence_surface,
    finland_corrigendum_unsupported_patch_frontier_item,
    corrigendum_source_witness,
    finland_corrigendum_sources_evidence_surface,
    finlex_html_topology_source_witness,
    finland_corrigendum_review_evidence_surface,
    finland_evidence_bundle_evidence_surface,
    finland_frontier_proof_evidence_surface,
    finland_he_branch_evidence_surface,
    finland_strict_report_ownership_closure_certificate,
    finland_strict_report_evidence_surface,
    finlex_editorial_witness_agreement_residual_rows,
    mutation_boundary_proof_rows,
    source_adjudication_agreement_residual_rows,
    source_adjudication_lineage_source_witness_rows,
    source_pathology_execution_authorization,
    source_pathology_frontier_work_item,
    source_pathology_proof_rule,
    source_pathology_proof_surface_rows,
    sparse_slot_candidate_set_certificate_rows,
    recovery_execution_authorization_rows_from_projection_rows,
    source_completeness_status_row,
    temporal_resolution_evidence_rows_from_projection_rows,
)


def test_finland_proof_surface_required_claim_kinds_are_registered() -> None:
    proof_surface_path = Path("src/lawvm/finland/proof_surfaces.py")
    tree = ast.parse(proof_surface_path.read_text(encoding="utf-8"))
    required = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith("fi.v1.")
    }

    missing = required - set(list_registered_kinds())

    assert missing == set()


def _consolidated_xml() -> bytes:
    return b"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <act>
    <meta>
      <identification>
        <FRBRWork>
          <FRBRthis value="/akn/fi/act/statute-consolidated/2014/1429/fin@20190112/!main"/>
        </FRBRWork>
        <FRBRExpression>
          <FRBRlanguage language="fin"/>
          <FRBRversionNumber value="20251497"/>
        </FRBRExpression>
        <FRBRManifestation>
          <FRBRdate name="dateConsolidated" date="2024-12-19"/>
        </FRBRManifestation>
      </identification>
    </meta>
  </act>
</akomaNtoso>
"""


def test_consolidated_artifact_source_witness_uses_embedded_artifact_identity() -> None:
    xml = _consolidated_xml()
    locator = "finlex://sd-cons-old/2014/1429/fin@20251497/main.xml"

    witness = consolidated_artifact_source_witness(locator=locator, xml_bytes=xml).to_dict()

    assert witness["source_role"] == "finlex_consolidated_oracle"
    assert witness["artifact_id"] == "2014/1429"
    assert witness["locator"] == locator
    assert witness["source_path"] == locator
    assert witness["version_id"] == "20190112"
    assert witness["source_lane"] == "sd-cons-old"
    assert witness["digest_algorithm"] == "sha256"
    assert witness["digest"] == hashlib.sha256(xml).hexdigest()
    assert witness["preview_digest_algorithm"] == "sha256"
    assert witness["preview_digest"]
    assert witness["path_version"] == "20251497"
    assert witness["embedded_version_tag"] == "20190112"
    assert witness["date_consolidated"] == "2024-12-19"
    assert source_witness_digest_coverage(witness) == "artifact_and_preview_digest"


def test_corrigendum_source_witness_carries_pdf_digest_and_preview() -> None:
    row = {
        "source_pdf": "akn/fi/act/statute-consolidated/1999/132/media/corrigenda/sk20140041_1.pdf",
        "pdf_name": "sk20140041_1.pdf",
        "statute_id": "1999/132",
        "amendment_id": "41/2013",
        "lang": "fi",
        "date_published": "6.3.2014",
        "date_status": "present",
        "correction_item_count": 1,
        "sha256": "d97b0330313cd3cd12358381380c216a696d520df7205e8e0247492c7c03f97e",
        "size_bytes": 51642,
    }

    witness = corrigendum_source_witness(row).to_dict()

    assert witness["source_role"] == "finland_corrigendum_pdf"
    assert witness["artifact_id"] == row["source_pdf"]
    assert witness["source_unit_id"] == "41/2013"
    assert witness["locator"] == row["source_pdf"]
    assert witness["digest_algorithm"] == "sha256"
    assert witness["digest"] == row["sha256"]
    assert witness["preview_digest_algorithm"] == "sha256"
    assert witness["source_lane"] == "corrigendum_pdf"
    assert witness["correction_item_count"] == 1
    assert source_witness_digest_coverage(witness) == "artifact_and_preview_digest"


def test_finland_bench_run_evidence_surface_declares_agreement_only_boundary() -> None:
    report = finland_bench_run_evidence_surface(
        {
            "label": "demo",
            "timestamp": "2026-06-06T12:00",
            "mode": "official_consolidation",
            "corpus_path": "data/finland/bench_corpus.csv",
            "run_path": "data/bench_runs/demo.csv",
            "history_path": "data/benchmark_history.csv",
            "stats": {
                "n": 2,
                "errors": 0,
                "mean": 0.99,
                "perfect": 1,
                "above_99": 1,
                "above_95": 2,
                "below_90": 0,
            },
            "status_counts": {"OK": 2},
            "diagnostic_summary_counts": {"diagnostics: source_pathologyx1": 1},
            "diagnostic_summary_row_count": 1,
            "section_score": False,
            "levenshtein_score": True,
            "worker_count": 8,
            "fast_mode": False,
            "diagnostic_replay": False,
        }
    )

    assert report["report_kind"] == "finland_bench_run"
    assert report["agreement_claims"] is True
    assert report["replay_claims"] is False
    assert report["canonical_effect_claims"] is False
    assert report["summary"]["statute_count"] == 2
    assert report["summary"]["status_counts"] == {"OK": 2}
    assert report["written_paths"] == ["data/bench_runs/demo.csv", "data/benchmark_history.csv"]
    assert "bench_score_as_replay_authorization" in report["forbidden_shortcuts"]


def test_finland_corrigendum_open_manual_evidence_surface_is_frontier_listing_only() -> None:
    report = finland_corrigendum_open_manual_evidence_surface(
        {
            "rows": [
                {
                    "amendment_id": "442/2016",
                    "db_row_count": 4,
                    "db_no_match_rows": 2,
                    "open_manual_rows": 1,
                    "attachment_only_rows": 0,
                    "manual_entry_count": 0,
                }
            ],
            "limit": 20,
            "include_all": False,
        }
    )

    assert report["report_kind"] == "finland_corrigendum_open_manual"
    assert report["replay_claims"] is False
    assert report["agreement_claims"] is False
    assert report["summary"]["candidate_count"] == 1
    assert report["summary"]["frontier_work_item_count"] == 1
    assert report["summary"]["open_manual_row_count"] == 1
    rows_by_surface = {row["surface"]: row for row in report["rows"]}
    assert set(rows_by_surface) == {
        "corrigendum_open_manual_candidate",
        "corrigendum_open_manual_frontier_work_item",
    }
    frontier = rows_by_surface["corrigendum_open_manual_frontier_work_item"]
    assert frontier["frontier_family"] == "fi_corrigendum_open_manual_candidate"
    assert frontier["frontier_status"] == "manual_claim_needed"
    assert frontier["required_claim_kind"] == "fi.v1.CORRIGENDUM_SOURCE_CORRECTION"
    assert frontier["executable"] is False
    assert frontier["replay_authorized"] is False
    assert frontier["authorization_status"] == "blocked_manual_claim_required"
    assert "open_manual_candidate_as_manual_claim" in report["forbidden_shortcuts"]


def test_finlex_html_topology_source_witness_is_preview_only() -> None:
    row = {
        "mismatch": True,
        "missing_from_xml": ["4 a §"],
        "extra_in_xml": ["section:7"],
        "html_error": "",
        "noncommensurable_reason": "",
        "html_url": "https://www.finlex.fi/fi/laki/ajantasa/1999/19990132",
    }

    witness = finlex_html_topology_source_witness(row, statute_id="1999/132").to_dict()

    assert witness["source_role"] == "finlex_html_topology_audit"
    assert witness["artifact_id"] == "1999/132"
    assert witness["source_unit_id"] == "1999/132"
    assert witness["locator"] == row["html_url"]
    assert witness["version_id"] == "live"
    assert witness["source_lane"] == "finlex_html_live_audit"
    assert witness["missing_from_xml_count"] == 1
    assert witness["extra_in_xml_count"] == 1
    assert witness["preview_digest_algorithm"] == "sha256"
    assert source_witness_digest_coverage(witness) == "preview_digest"


def test_finland_evidence_bundle_projects_passive_shared_report() -> None:
    html_witness = finlex_html_topology_source_witness(
        {
            "mismatch": True,
            "missing_from_xml": ["4 a §"],
            "extra_in_xml": [],
            "html_error": "",
            "noncommensurable_reason": "",
            "html_url": "https://www.finlex.fi/fi/laki/ajantasa/1999/19990132",
        },
        statute_id="1999/132",
    ).to_dict()
    corrigendum_witness = corrigendum_source_witness(
        {
            "source_pdf": "akn/fi/act/statute-consolidated/1999/132/media/corrigenda/sk20140041_1.pdf",
            "pdf_name": "sk20140041_1.pdf",
            "statute_id": "1999/132",
            "amendment_id": "41/2013",
            "date_published": "6.3.2014",
            "correction_item_count": 1,
            "sha256": "d97b0330313cd3cd12358381380c216a696d520df7205e8e0247492c7c03f97e",
        }
    ).to_dict()

    report = finland_evidence_bundle_evidence_surface(
        {
            "statute_id": "1999/132",
            "mode": "legal_pit",
            "overall_score": 0.9,
            "section_score": 0.8,
            "html_topology": {"source_witness": html_witness},
            "supporting_amendments": [{"amendment_id": "41/2013", "source_witnesses": [corrigendum_witness]}],
            "proof_claims": [{"tier": "PROVED_ORACLE_INCORRECT", "kind": "xml_html_topology_drift"}],
            "section_claims": [{"section": "section:4"}],
            "source_pathologies": [{"code": "SPARSE_ITEM_BODY_MISSING"}],
            "evidence_context_diagnostics": [{"surface": "body_pairing", "error": "unavailable"}],
            "section_bisect": [{"section": "section:4"}],
            "compiler_observations": {"normalized_section_observation_count": 3},
            "proof_tiers": ["PROVED_ORACLE_INCORRECT"],
            "primary_proof_tier": "PROVED_ORACLE_INCORRECT",
        }
    )

    assert report["jurisdiction"] == "fi"
    assert report["report_kind"] == "finland_evidence_bundle"
    assert report["replay_claims"] is False
    assert report["canonical_effect_claims"] is False
    assert report["candidate_effect_claims"] is False
    assert report["dry_run_claims"] is False
    assert report["agreement_claims"] is True
    assert report["summary"]["proof_claim_count"] == 1
    assert report["summary"]["section_claim_count"] == 1
    assert report["summary"]["source_pathology_count"] == 1
    assert report["summary"]["source_pathology_kind_counts"] == {
        "SPARSE_ITEM_BODY_MISSING": 1
    }
    assert report["summary"]["source_pathology_affected_phase_counts"] == {
        "typed_elaboration": 1
    }
    assert report["summary"]["html_topology_source_witness_count"] == 1
    assert report["summary"]["corrigendum_source_witness_count"] == 1
    assert report["summary"]["source_witness_digest_coverage_counts"] == {
        "artifact_and_preview_digest": 1,
        "preview_digest": 1,
    }
    assert {row["surface"] for row in report["rows"]} == {
        "evidence_context_diagnostic",
        "proof_claim",
        "source_pathology",
        "source_witness",
    }
    pathology_row = next(row for row in report["rows"] if row["surface"] == "source_pathology")
    assert pathology_row["replay_authorized"] is False
    assert pathology_row["pathology_kind"] == "SPARSE_ITEM_BODY_MISSING"
    assert pathology_row["suggested_lane"] == "source_pathology"
    assert "proof_claim_as_mutation_instruction" in report["forbidden_shortcuts"]


def test_finland_frontier_proof_report_projects_shared_envelope() -> None:
    report = finland_frontier_proof_evidence_surface(
        rows=(
            {
                "statute_id": "1999/132",
                "bucket": "candidate",
                "score": 0.7,
                "primary_proof_tier": "UNRESOLVED",
                "proof_tiers": ["UNRESOLVED"],
                "proof_kinds": ["no_strong_claim"],
            },
        ),
        summary={
            "primary_tiers": {"UNRESOLVED": 1},
            "proof_kinds": {"no_strong_claim": 1},
            "section_claim_kinds": {},
            "statute_only_proof_kinds": {"no_strong_claim": 1},
            "bucket_primary_tiers": {"candidate:UNRESOLVED": 1},
        },
        label="fi_frontier",
        mode="legal_pit",
        top=30,
        bucket_filter="candidate",
    )

    assert report["jurisdiction"] == "fi"
    assert report["report_kind"] == "finland_frontier_proof_report"
    assert report["replay_claims"] is False
    assert report["canonical_effect_claims"] is False
    assert report["candidate_effect_claims"] is False
    assert report["dry_run_claims"] is False
    assert report["agreement_claims"] is True
    assert report["filters"] == {
        "label": "fi_frontier",
        "mode": "legal_pit",
        "top": 30,
        "bucket_filter": "candidate",
    }
    assert report["summary"]["frontier_proof_row_count"] == 1
    assert report["summary"]["primary_tiers"] == {"UNRESOLVED": 1}
    assert report["summary"]["proof_kinds"] == {"no_strong_claim": 1}
    assert report["rows"][0]["surface"] == "frontier_proof_row"
    assert "frontier_rank_as_replay_authorization" in report["forbidden_shortcuts"]


def test_finland_he_branch_evidence_surface_keeps_proposals_non_enacted() -> None:
    parsed = HEParsedBranch(
        branch_id="fi/he/2026/1",
        he_id="HE 1/2026 vp",
        he_year=2026,
        he_number=1,
        proposed_voimaantulo=date(2026, 9, 1),
        proposed_ops=(
            BranchProposedOp(
                op_index=0,
                operation_kind="insert",
                target_provision_ref="711/2022/4a",
                target_statute_id="711/2022",
                payload_summary="uusi 4 a §",
                source_he_id="HE 1/2026 vp",
                branch_id="fi/he/2026/1",
                source_span_text="Ehdotetaan, että lannoitelakiin lisätään uusi 4 a §.",
                source_span_preamble="Ehdotetaan, että",
                target_resolution=BranchTargetResolution.PROPOSAL_RELATIVE,
                parse_confidence=0.8,
                is_proposal_relative=True,
            ),
        ),
        target_statute_ids=("711/2022",),
        parse_status=HEParseStatus.PARTIAL,
        parse_findings=(
            BranchTargetResolutionFinding(
                rule_id="HE_BRANCH.TARGET_PROPOSAL_RELATIVE",
                op_index=0,
                target_provision_ref="711/2022/4a",
                target_statute_id="711/2022",
                reason="new provision is proposal-relative",
                is_proposal_relative=True,
            ),
            BranchParseRecovery(
                rule_id="HE_BRANCH.CLAUSE_PARSE_ERROR",
                op_index=1,
                clause_text="unparseable clause",
                reason="unsupported clause shape",
                detail="fixture",
            ),
        ),
        enactment_sections_found=1,
        clauses_attempted=2,
        clauses_succeeded=1,
    )

    report = finland_he_branch_evidence_surface(parsed)

    assert report["report_kind"] == "finland_he_branch"
    assert report["replay_claims"] is False
    assert report["canonical_effect_claims"] is False
    assert report["candidate_effect_claims"] is False
    assert report["dry_run_claims"] is False
    assert report["agreement_claims"] is False
    assert report["summary"]["proposed_op_count"] == 1
    assert report["summary"]["branch_impact_projection_count"] == 1
    assert report["summary"]["branch_impact_row_count"] == 1
    assert report["summary"]["parse_finding_count"] == 2
    assert report["summary"]["proposal_relative_op_count"] == 1
    assert report["summary"]["unresolved_target_finding_count"] == 1
    assert report["filters"] == {
        "branch_id": "fi/he/2026/1",
        "he_id": "HE 1/2026 vp",
        "parse_status": "partial",
    }
    assert [row["surface"] for row in report["rows"]] == [
        "he_branch_proposed_op",
        "he_branch_target_resolution_finding",
        "he_branch_parse_finding",
        "he_branch_impact_projection",
    ]
    proposed = report["rows"][0]
    assert proposed["status"] == "proposed_branch_op_not_enacted_authority"
    assert proposed["target_resolution"] == "proposal_relative"
    assert proposed["execution_authorization"]["executable"] is False
    assert proposed["execution_authorization"]["replay_authorized"] is False
    assert proposed["execution_authorization"]["authorization_status"] == "he_branch_proposal_not_replay_authority"
    target_finding = report["rows"][1]
    assert target_finding["owner_phase"] == "target_resolution"
    assert target_finding["execution_authorization"]["authorization_status"] == "he_branch_finding_not_replay_authority"
    branch_projection = report["rows"][3]
    assert branch_projection["status"] == "branch_projection_not_enacted_authority"
    assert branch_projection["executable"] is False
    assert branch_projection["replay_authorized"] is False
    assert branch_projection["projection"]["status"] == "diagnostic_only"
    assert branch_projection["projection"]["branch"]["authority_layer"] == "proposal"
    assert branch_projection["projection"]["rows"][0]["edge_kind"] == "would_insert"
    assert branch_projection["projection"]["rows"][0]["target_statute_id"] == "711/2022"
    assert "he_branch_op_as_enacted_operation" in report["forbidden_shortcuts"]
    assert "he_branch_target_resolution_as_target_hijack" in proposed["forbidden_shortcuts"]


def test_finland_corrigendum_review_projects_source_diagnostic_envelope() -> None:
    witness = corrigendum_source_witness(
        {
            "source_pdf": "akn/fi/act/statute-consolidated/1999/132/media/corrigenda/sk20140041_1.pdf",
            "pdf_name": "sk20140041_1.pdf",
            "statute_id": "1999/132",
            "amendment_id": "41/2013",
            "date_published": "6.3.2014",
            "correction_item_count": 1,
            "sha256": "d97b0330313cd3cd12358381380c216a696d520df7205e8e0247492c7c03f97e",
        }
    ).to_dict()

    report = finland_corrigendum_review_evidence_surface(
        {
            "statute_id": "1999/132",
            "mode": "legal_pit",
            "source_pathologies": [{"code": "DESTRUCTIVE_SHAPE_LOSS_RISK"}],
            "contingent_effective_sources": ["41/2013"],
            "amendments": [
                {
                    "amendment_id": "41/2013",
                    "corrigendum_db_rows": 2,
                    "corrigendum_no_match_rows": 1,
                    "corrigendum_verified_rows": 1,
                    "manual_override_count": 0,
                    "manual_template_entry_count": 1,
                    "source_witnesses": [witness],
                }
            ],
            "unblamed_sections": [{"section": "section:4", "diagnosis": "REPLAY_MISSING"}],
        }
    )

    assert report["jurisdiction"] == "fi"
    assert report["report_kind"] == "finland_corrigendum_review"
    assert report["replay_claims"] is False
    assert report["canonical_effect_claims"] is False
    assert report["candidate_effect_claims"] is False
    assert report["dry_run_claims"] is False
    assert report["agreement_claims"] is True
    assert report["summary"]["amendment_count"] == 1
    assert report["summary"]["source_pathology_count"] == 1
    assert report["summary"]["source_pathology_kind_counts"] == {
        "DESTRUCTIVE_SHAPE_LOSS_RISK": 1
    }
    assert report["summary"]["source_pathology_suggested_lane_counts"] == {
        "replay_recovery_risk": 1
    }
    assert report["summary"]["corrigendum_source_witness_count"] == 1
    assert report["summary"]["corrigendum_source_witness_digest_coverage_counts"] == {
        "artifact_and_preview_digest": 1
    }
    assert {row["surface"] for row in report["rows"]} == {
        "corrigendum_review_amendment",
        "corrigendum_source_witness",
        "source_pathology",
        "unblamed_section",
    }
    pathology_row = next(row for row in report["rows"] if row["surface"] == "source_pathology")
    assert pathology_row["replay_authorized"] is False
    assert pathology_row["pathology_kind"] == "DESTRUCTIVE_SHAPE_LOSS_RISK"
    assert "corrigendum_source_witness_as_patch_application" in report["forbidden_shortcuts"]


def test_finland_corrigendum_provenance_projects_source_diagnostic_envelope() -> None:
    witness = corrigendum_source_witness(
        {
            "source_pdf": "akn/fi/act/statute-consolidated/2016/442/media/corrigenda/sk20160442_1.pdf",
            "pdf_name": "sk20160442_1.pdf",
            "statute_id": "2016/442",
            "amendment_id": "442/2016",
            "date_published": "31.5.2016",
            "correction_item_count": 2,
            "sha256": "b" * 64,
        }
    ).to_dict()

    report = finland_corrigendum_provenance_evidence_surface(
        {
            "amendment_id": "442/2016",
            "verified_count": 1,
            "attachment_only_count": 0,
            "manual_exact_count": 1,
            "open_manual_candidate_count": 0,
            "manual_entry_count": 1,
            "source_witnesses": [witness],
            "rows": [
                {
                    "stable_id": "sk20160442_1.pdf#0",
                    "status": "source_verified",
                    "source_witness": witness,
                },
                {
                    "stable_id": "sk20160442_1.pdf#1",
                    "status": "manual_override_exact",
                    "source_witness": witness,
                },
            ],
        }
    )

    assert report["jurisdiction"] == "fi"
    assert report["report_kind"] == "finland_corrigendum_provenance"
    assert report["replay_claims"] is False
    assert report["canonical_effect_claims"] is False
    assert report["candidate_effect_claims"] is False
    assert report["dry_run_claims"] is False
    assert report["agreement_claims"] is False
    assert report["summary"]["provenance_row_count"] == 2
    assert report["summary"]["source_witness_count"] == 1
    assert report["summary"]["status_counts"] == {
        "manual_override_exact": 1,
        "source_verified": 1,
    }
    assert report["summary"]["source_witness_digest_coverage_counts"] == {
        "artifact_and_preview_digest": 1
    }
    assert {row["surface"] for row in report["rows"]} == {
        "corrigendum_provenance_row",
        "corrigendum_source_witness",
    }
    assert "corrigendum_provenance_as_replay_authorization" in report["forbidden_shortcuts"]


def test_finland_corrigendum_overview_projects_corpus_diagnostic_envelope() -> None:
    report = finland_corrigendum_overview_evidence_surface(
        {
            "mode": "live",
            "limit": 10,
            "official_item_count": 12,
            "amendment_count": 5,
            "source_pdf_count": 4,
            "missing_amendment_id_count": 0,
            "missing_date_published_count": 3,
            "source_date_status_counts": {"present": 2, "xml_ref_without_date": 2},
            "type_counts": {"johtolause": 5, "prose": 7},
            "status_counts": {
                "source_verified": 8,
                "open_manual_candidate": 2,
                "unresolved_unverified": 1,
                "unresolved_unreviewed": 1,
            },
            "top_unresolved_amendments": [{"amendment_id": "577/2019", "item_count": 6}],
            "top_open_manual_amendments": [{"amendment_id": "442/2016", "item_count": 2}],
            "top_attachment_only_amendments": [{"amendment_id": "700/2020", "item_count": 1}],
        }
    )

    assert report["jurisdiction"] == "fi"
    assert report["report_kind"] == "finland_corrigendum_overview"
    assert report["replay_claims"] is False
    assert report["canonical_effect_claims"] is False
    assert report["candidate_effect_claims"] is False
    assert report["dry_run_claims"] is False
    assert report["agreement_claims"] is False
    assert report["summary"]["official_item_count"] == 12
    assert report["summary"]["open_manual_candidate_count"] == 2
    assert report["summary"]["unresolved_unverified_count"] == 1
    assert report["summary"]["top_unresolved_amendment_count"] == 1
    assert report["summary"]["top_open_manual_amendment_count"] == 1
    assert report["summary"]["top_attachment_only_amendment_count"] == 1
    assert report["summary"]["source_completeness_status_count"] == 1
    assert report["summary"]["source_completeness"] == {
        "chain_length": 4,
        "source_available": 4,
        "dates_available": 1,
        "missing_sources": 0,
        "missing_dates": 3,
    }
    assert {row["surface"] for row in report["rows"]} == {
        "corrigendum_overview_attachment_only_amendment",
        "corrigendum_overview_open_manual_amendment",
        "corrigendum_overview_unresolved_amendment",
        "source_completeness_status",
    }
    rows_by_surface = {row["surface"]: row for row in report["rows"]}
    source_status = rows_by_surface["source_completeness_status"]
    assert source_status["status"] == "incomplete"
    assert source_status["owner_phase"] == "source_acquisition"
    assert source_status["replay_authorized"] is False
    assert "status_count_as_manual_claim" in report["forbidden_shortcuts"]


def test_finland_corrigendum_manual_template_projects_frontier_work_item() -> None:
    witness = corrigendum_source_witness(
        {
            "source_pdf": "akn/fi/act/statute-consolidated/2012/991/media/corrigenda/sk20120991_1.pdf",
            "pdf_name": "sk20120991_1.pdf",
            "statute_id": "2012/991",
            "amendment_id": "991/2012",
            "date_published": "1.1.2013",
            "correction_item_count": 1,
            "sha256": "c" * 64,
        }
    ).to_dict()
    entry = {
        "amendment_id": "991/2012",
        "wrong_text": "old",
        "correct_text": "new",
        "correction_type": "johtolause",
        "notes": "current_verify=False",
        "verified": "",
    }

    item = finland_corrigendum_manual_template_frontier_item(
        amendment_id="991/2012",
        entry_index=0,
        entry=entry,
        source_witness=witness,
    ).to_dict()

    assert item["jurisdiction"] == "fi"
    assert item["frontier_family"] == "fi_corrigendum_manual_override"
    assert item["frontier_status"] == "manual_claim_needed"
    assert item["required_claim_kind"] == "fi.v1.CORRIGENDUM_SOURCE_CORRECTION"
    assert item["executable"] is False
    assert item["replay_authorized"] is False
    assert item["authorization_status"] == "blocked_manual_claim_required"
    assert item["source_witness"]["digest"] == "c" * 64
    assert "manual_template_entry_as_manual_claim" in item["forbidden_shortcuts"]


def test_finland_corrigendum_unsupported_patch_projects_frontier_work_item() -> None:
    witness = corrigendum_source_witness(
        {
            "source_pdf": "akn/fi/act/statute-consolidated/2013/23/media/corrigenda/sk20160442_1.pdf",
            "pdf_name": "sk20160442_1.pdf",
            "statute_id": "2013/23",
            "amendment_id": "442/2016",
            "date_published": "2016-06-01",
            "correction_item_count": 1,
            "sha256": "e" * 64,
        }
    ).to_dict()
    patch = {
        "amendment_id": "2016/442",
        "sequence": 1,
        "correction_kind": "ADD",
        "location": "Sivulla 1, johtolauseesta puuttuu virke, joka kuuluu",
        "target": "preamble:formula",
        "correct_text": "lisätty teksti",
        "reason": "FINLAND.CORRIGENDUM_ADD_UNSUPPORTED",
        "source_statute": "corr/442/2016",
    }

    item = finland_corrigendum_unsupported_patch_frontier_item(
        patch=patch,
        source_witness=witness,
    ).to_dict()

    assert item["jurisdiction"] == "fi"
    assert item["frontier_family"] == "fi_corrigendum_add_unsupported"
    assert item["frontier_status"] == "unsupported_corrigendum_patch_frontier"
    assert item["required_claim_kind"] == "fi.v1.CORRIGENDUM_UNSUPPORTED_PATCH_RESOLUTION"
    assert item["executable"] is False
    assert item["replay_authorized"] is False
    assert item["authorization_status"] == "blocked_unsupported_corrigendum_patch"
    assert item["source_witness"]["digest"] == "e" * 64
    assert item["target_witness"]["target"] == "preamble:formula"
    assert "unsupported_corrigendum_patch_as_manual_claim" in item["forbidden_shortcuts"]


def test_finland_corrigendum_unsupported_patch_evidence_surface_projects_frontier_envelope() -> None:
    witness = corrigendum_source_witness(
        {
            "source_pdf": "akn/fi/act/statute-consolidated/2013/23/media/corrigenda/sk20160442_1.pdf",
            "pdf_name": "sk20160442_1.pdf",
            "statute_id": "2013/23",
            "amendment_id": "442/2016",
            "date_published": "2016-06-01",
            "correction_item_count": 1,
            "sha256": "f" * 64,
        }
    ).to_dict()
    patch = {
        "amendment_id": "2016/442",
        "correction_type": "table",
        "location_desc": "Sivu 2, taulukko 1",
        "wrong_text": "1 | old",
        "correct_text": "1 | new",
        "reason": "FINLAND.CORRIGENDUM_TABLE_UNSUPPORTED",
    }

    report = finland_corrigendum_unsupported_patch_evidence_surface(
        {
            "amendment_id": "2016/442",
            "patches": [patch],
            "source_witnesses": [witness],
        }
    )

    assert report["jurisdiction"] == "fi"
    assert report["report_kind"] == "finland_corrigendum_unsupported_patch"
    assert report["replay_claims"] is False
    assert report["canonical_effect_claims"] is False
    assert report["candidate_effect_claims"] is False
    assert report["dry_run_claims"] is False
    assert report["agreement_claims"] is False
    assert report["summary"]["unsupported_patch_count"] == 1
    assert report["summary"]["frontier_work_item_count"] == 1
    assert report["summary"]["source_witness_digest_coverage_counts"] == {
        "artifact_and_preview_digest": 1
    }
    assert report["summary"]["reason_counts"] == {"FINLAND.CORRIGENDUM_TABLE_UNSUPPORTED": 1}
    assert {row["surface"] for row in report["rows"]} == {
        "corrigendum_source_witness",
        "corrigendum_unsupported_patch",
        "corrigendum_unsupported_patch_frontier_work_item",
    }
    assert "unsupported_corrigendum_patch_as_replay_authorization" in report["forbidden_shortcuts"]


def test_finland_corrigendum_manual_template_projects_frontier_envelope() -> None:
    witness = corrigendum_source_witness(
        {
            "source_pdf": "akn/fi/act/statute-consolidated/2012/991/media/corrigenda/sk20120991_1.pdf",
            "pdf_name": "sk20120991_1.pdf",
            "statute_id": "2012/991",
            "amendment_id": "991/2012",
            "date_published": "1.1.2013",
            "correction_item_count": 1,
            "sha256": "c" * 64,
        }
    ).to_dict()
    entry = {
        "amendment_id": "991/2012",
        "wrong_text": "old",
        "correct_text": "new",
        "correction_type": "johtolause",
        "notes": "current_verify=False",
        "verified": "",
    }
    frontier = finland_corrigendum_manual_template_frontier_item(
        amendment_id="991/2012",
        entry_index=0,
        entry=entry,
        source_witness=witness,
    ).to_dict()

    report = finland_corrigendum_manual_template_evidence_surface(
        {
            "amendment_id": "991/2012",
            "include_all": False,
            "manual_entry_count": 0,
            "already_covered": False,
            "attachment_only_entry_count": 0,
            "source_witnesses": [witness],
            "frontier_work_items": [frontier],
            "entries": [entry],
        }
    )

    assert report["jurisdiction"] == "fi"
    assert report["report_kind"] == "finland_corrigendum_manual_template"
    assert report["replay_claims"] is False
    assert report["canonical_effect_claims"] is False
    assert report["candidate_effect_claims"] is False
    assert report["dry_run_claims"] is False
    assert report["agreement_claims"] is False
    assert report["summary"]["entry_count"] == 1
    assert report["summary"]["frontier_work_item_count"] == 1
    assert report["summary"]["source_witness_count"] == 1
    assert report["summary"]["source_witness_digest_coverage_counts"] == {
        "artifact_and_preview_digest": 1
    }
    assert {row["surface"] for row in report["rows"]} == {
        "corrigendum_manual_template_entry",
        "corrigendum_manual_template_frontier_work_item",
        "corrigendum_source_witness",
    }
    assert "manual_template_entry_as_manual_claim" in report["forbidden_shortcuts"]


def test_finland_corrigendum_sources_projects_source_manifest_envelope() -> None:
    record = {
        "source_pdf": "akn/fi/act/statute-consolidated/2013/23/media/corrigenda/sk20160442_1.pdf",
        "pdf_name": "sk20160442_1.pdf",
        "statute_id": "2013/23",
        "amendment_id": "442/2016",
        "lang": "fi",
        "date_published": "2016-06-01",
        "date_status": "present",
        "correction_item_count": 2,
        "sha256": "d" * 64,
        "size_bytes": 321,
    }
    witness = corrigendum_source_witness(record).to_dict()

    report = finland_corrigendum_sources_evidence_surface(
        {
            "mode": "stored",
            "limit": 1,
            "pdf_count": 3,
            "amendment_count": 2,
            "total_item_count": 5,
            "date_status_counts": {"present": 2, "xml_ref_without_date": 1},
            "records_truncated": True,
            "source_witnesses": [witness],
            "records": [record],
        }
    )

    assert report["jurisdiction"] == "fi"
    assert report["report_kind"] == "finland_corrigendum_sources"
    assert report["replay_claims"] is False
    assert report["canonical_effect_claims"] is False
    assert report["candidate_effect_claims"] is False
    assert report["dry_run_claims"] is False
    assert report["agreement_claims"] is False
    assert report["rows_truncated"] is True
    assert report["summary"]["pdf_count"] == 3
    assert report["summary"]["shown_record_count"] == 1
    assert report["summary"]["source_witness_count"] == 1
    assert report["summary"]["missing_date_count"] == 1
    assert report["summary"]["source_completeness_status_count"] == 1
    assert report["summary"]["source_completeness"] == {
        "chain_length": 3,
        "source_available": 3,
        "dates_available": 2,
        "missing_sources": 0,
        "missing_dates": 1,
    }
    assert report["summary"]["source_bundle_assertion_count"] == 1
    assert report["summary"]["source_bundle_admission_count"] == 1
    assert report["summary"]["source_bundle_admitted_count"] == 1
    assert report["summary"]["source_bundle_status_counts"] == {"source_bundle_admitted": 1}
    assert report["summary"]["source_witness_digest_coverage_counts"] == {
        "artifact_and_preview_digest": 1
    }
    assert {row["surface"] for row in report["rows"]} == {
        "corrigendum_source_manifest_record",
        "corrigendum_source_witness",
        "source_acquisition_assertion",
        "source_bundle_admission",
        "source_completeness_status",
    }
    rows_by_surface = {row["surface"]: row for row in report["rows"]}
    source_admission = rows_by_surface["source_bundle_admission"]
    assert source_admission["admitted"] is True
    assert source_admission["execution_authorization"]["executable"] is False
    assert source_admission["execution_authorization"]["replay_authorized"] is False
    assert (
        source_admission["execution_authorization"]["authorization_status"]
        == "source_bundle_admitted_not_replay_authority"
    )
    source_status = rows_by_surface["source_completeness_status"]
    assert source_status["status"] == "incomplete"
    assert source_status["owner_phase"] == "source_acquisition"
    assert source_status["replay_authorized"] is False
    assert source_status["execution_authorization"]["replay_authorized"] is False
    assert "source_manifest_as_replay_authorization" in report["forbidden_shortcuts"]
    assert "source_bundle_admission_as_replay_authorization" in report["forbidden_shortcuts"]


def test_mutation_boundary_reports_project_shared_proof_rows() -> None:
    report = MutationInvariantReport(
        op_id="op-1",
        helper="_apply_deterministic_subsection_op",
        outcome="applied",
        touched_paths=((("chapter", "1"), ("section", "2")),),
        changed_paths=((("chapter", "1"), ("section", "2")),),
        allowed_roots=((("chapter", "1"), ("section", "2")),),
        allowed_effect_region_paths=((("chapter", "1"), ("section", "2")),),
        permitted_paths=((("chapter", "1"), ("section", "2")),),
        covered_changed_paths=((("chapter", "1"), ("section", "2")),),
        path_set_invariant_holds=True,
    )

    rows = mutation_boundary_proof_rows((report,), statute_id="2001/1234")

    assert len(rows) == 1
    proof = rows[0]
    assert proof["jurisdiction"] == "fi"
    assert proof["materialization_surface"] == "finland_strict_report"
    assert proof["owner_phase"] == "replay_apply"
    assert proof["operation_id"] == "op-1"
    assert proof["status"] == "proved"
    assert proof["path_set_invariant_holds"] is True
    assert "mutation_boundary_report_as_replay_authorization" in proof["forbidden_shortcuts"]


def test_serialized_mutation_boundary_reports_project_shared_proof_rows() -> None:
    rows = mutation_boundary_proof_rows(
        (
            {
                "op_id": "op-2",
                "helper": "apply_op",
                "outcome": "failed",
                "touched_paths": [[["chapter", "1"], ["section", "9"]]],
                "changed_paths": [[["chapter", "1"], ["section", "9"]]],
                "results": [
                    {
                        "code": "REPLAY_FAILED_OP_MUTATED_TREE",
                        "op_id": "op-2",
                        "helper": "apply_op",
                        "touched_count": 1,
                    }
                ],
                "source_statute": "2010/100",
            },
        ),
        statute_id="2001/1234",
    )

    proof = rows[0]
    assert proof["operation_id"] == "op-2"
    assert proof["status"] == "violated"
    assert proof["source_artifact_id"] == "2010/100"
    assert proof["result_codes"] == ["REPLAY_FAILED_OP_MUTATED_TREE"]


def test_source_pathology_rule_owns_high_value_family() -> None:
    rule = source_pathology_proof_rule("DESTRUCTIVE_SHAPE_LOSS_RISK")

    assert rule.owner_phase == "replay_apply"
    assert rule.frontier_family == "fi_destructive_shape_loss_risk"
    assert "mutation_boundary_proof_before_replay_promotion" in rule.required_proofs


def test_source_pathology_authorization_is_not_replay_authority() -> None:
    pathology = SourcePathology.from_scope(
        code="MALFORMED_BROAD_REPLACE_BODY",
        message="Broad replace source body is partial.",
        source_statute="2001/748",
        target_unit_kind="section",
        target_label="section 6",
        detail={"diagnostic_reason": "partial_body_only"},
    )

    authorization = source_pathology_execution_authorization(pathology).to_dict()

    assert authorization["executable"] is False
    assert authorization["replay_authorized"] is False
    assert authorization["owner_phase"] == "payload_normalization"
    assert authorization["strict_disposition"] == "block"
    assert authorization["quirks_disposition"] == "record"
    assert authorization["authorization_status"] == "source_pathology_not_replay_authority"
    assert "treat_source_pathology_as_replay_authorization" in authorization["forbidden_shortcuts"]


def test_source_pathology_frontier_work_item_is_non_executable() -> None:
    pathology = {
        "code": "SPARSE_ITEM_BODY_MISSING",
        "message": "Sparse omission payload did not reproduce the targeted item body.",
        "source_statute": "2020/1",
        "target_unit_kind": "section",
        "target_label": "section 5 subsection 2 item 3",
        "detail": {
            "target_section": "5",
            "target_paragraph": "2",
            "target_item": "3",
        },
    }

    item = source_pathology_frontier_work_item(pathology, statute_id="1999/1").to_dict()

    assert item["jurisdiction"] == "fi"
    assert item["frontier_family"] == "fi_sparse_item_body_missing"
    assert item["owner_phase"] == "typed_elaboration"
    assert item["executable"] is False
    assert item["replay_authorized"] is False
    assert item["authorization_status"] == "source_pathology_not_replay_authority"
    assert item["source_witness"]["source_role"] == "finland_source_pathology"
    assert item["source_witness"]["preview_digest_algorithm"] == "sha256"
    assert item["source_witness"]["preview_digest"]
    assert item["target_witness"]["target_label"] == "section 5 subsection 2 item 3"
    assert "validate_source_pathology_resolution_claim" in item["required_validator_checks"]
    assert item["detail"]["execution_authorization"]["replay_authorized"] is False


def test_source_pathology_proof_surface_rows_bundle_authorization_and_frontier() -> None:
    rows = source_pathology_proof_surface_rows(
        (
            {
                "code": "RECODIFICATION_SOURCE_CHAIN_GAP",
                "message": "Recodification source-chain gap.",
                "source_statute": "2024/1",
                "target_unit_kind": "chapter",
                "target_label": "chapter 2",
                "detail": {"diagnostic_reason": "pre_wave_source_missing"},
            },
        ),
        statute_id="1990/1",
    )

    assert len(rows["source_pathology_execution_authorizations"]) == 1
    assert len(rows["source_pathology_frontier_work_items"]) == 1
    assert rows["source_pathology_execution_authorizations"][0]["owner_phase"] == "source_chain_elaboration"
    assert rows["source_pathology_frontier_work_items"][0]["frontier_status"] == "source_chain_frontier"


def test_sparse_slot_binding_projects_partial_candidate_certificate() -> None:
    certificates = sparse_slot_candidate_set_certificate_rows(
        (
            {
                "kind": "ELAB.SPARSE_SLOT_BINDING",
                "detail": {
                    "source_statute": "2010/100",
                    "target_unit_kind": "section",
                    "target_norm": "3",
                    "target_chapter": "",
                    "op_description": "REPLACE 3 § 1 mom",
                    "op_type": "REPLACE",
                    "target_paragraph": 1,
                    "target_item": "",
                    "target_special": "",
                    "payload_slot_index": 1,
                    "payload_slot_label": "1",
                },
            },
        ),
        statute_id="1999/1",
    )

    assert len(certificates) == 1
    cert = certificates[0]
    assert cert["candidate_set_kind"] == "fi_sparse_payload_slot_assignment"
    assert cert["phase"] == "typed_elaboration"
    assert cert["completeness_status"] == "partial"
    assert cert["candidate_ids"] == ["payload-slot:1:1"]
    assert cert["selected_candidate_ids"] == ["payload-slot:1:1"]
    assert cert["next_promotion_allowed"] is False
    assert "slot_uniqueness_proof" in cert["next_promotion_requires"]


def test_sparse_leftover_projects_rejected_candidate_certificate() -> None:
    certificates = sparse_slot_candidate_set_certificate_rows(
        (
            {
                "kind": "ELAB.SPARSE_PAYLOAD_LEFTOVER",
                "detail": {
                    "source_statute": "2010/100",
                    "target_unit_kind": "section",
                    "target_norm": "3",
                    "target_chapter": "",
                    "unassigned_slots": ["2:2", "3:(unlabeled)"],
                },
            },
        ),
        statute_id="1999/1",
    )

    assert len(certificates) == 1
    cert = certificates[0]
    assert cert["completeness_status"] == "rejected"
    assert cert["candidate_count"] == 2
    assert cert["blocker_counts"] == {"unassigned_payload_slot": 2}
    assert cert["blocker_families"] == ["sparse_payload_leftover"]
    assert cert["candidate_ids"] == ["payload-slot:2:2", "payload-slot:3:unlabeled"]


def test_sparse_ambiguous_binding_projects_partial_candidate_certificate() -> None:
    certificates = sparse_slot_candidate_set_certificate_rows(
        (
            {
                "kind": "ELAB.AMBIGUOUS_BINDING",
                "detail": {
                    "slot_id": 2,
                    "amendment_id": "2010/100",
                    "candidate_count": 3,
                    "admissibility": "ambiguous",
                },
            },
        ),
        statute_id="1999/1",
    )

    assert len(certificates) == 1
    cert = certificates[0]
    assert cert["completeness_status"] == "partial"
    assert cert["candidate_count"] == 3
    assert cert["candidate_ids"] == ["payload-slot:2"]
    assert cert["blocker_counts"] == {"ambiguous_binding": 1}
    assert cert["blocker_families"] == ["sparse_slot_ambiguity"]


def test_finland_strict_report_evidence_surface_declares_claim_boundary() -> None:
    report = finland_strict_report_evidence_surface(
        {
            "statute_id": "2001/1234",
            "profile": "FINLAND_INGESTION_V1",
            "ops": {"canonical": 2, "failed": 1, "total": 3},
            "source_completeness": {
                "chain_length": 3,
                "source_available": 2,
                "dates_available": 1,
            },
            "source_pathologies": [
                {
                    "code": "DESTRUCTIVE_SHAPE_LOSS_RISK",
                    "message": "source pathology",
                    "source_statute": "2001/748",
                    "target_unit_kind": "section",
                    "target_label": "6 §",
                    "detail": {"diagnostic_reason": "partial_body_only"},
                }
            ],
            "source_pathology_execution_authorizations": [
                {"authorization_status": "source_pathology_not_replay_authority"}
            ],
            "source_pathology_frontier_work_items": [
                {
                    "frontier_family": "fi_destructive_shape_loss_risk",
                    "source_witness": {
                        "source_role": "finland_source_pathology",
                        "preview_digest": "abc123",
                    },
                }
            ],
            "sparse_slot_candidate_set_certificates": [{"candidate_set_kind": "fi_sparse_payload_slot_assignment"}],
            "agreement_residuals": [
                {
                    "residual_id": "fi:2001/1234:noncommensurable",
                    "jurisdiction": "fi",
                    "agreement_surface": "finlex_html_oracle_compare",
                    "family": "non_commensurable_surface",
                    "status": "residual",
                    "owner_phase": "oracle_adjudication",
                    "rule_id": "fi_finlex_html_non_commensurable_surface",
                    "source_artifact_id": "2001/1234",
                    "replay_count": 0,
                    "oracle_count": 0,
                    "missing_proofs": ["compare_projection_review"],
                    "safe_default": "classify_without_replay_promotion",
                    "forbidden_shortcuts": ["finlex_oracle_as_source_truth"],
                    "detail": {"html_noncommensurable_reason": "oracle_extra_scoped_labels"},
                }
            ],
            "mutation_boundary_proofs": [
                {
                    "proof_id": "fi:2001/1234:mutation-boundary:1:op-1",
                    "status": "proved",
                }
            ],
            "projection_rows": [
                {"kind": "ELAB.SPARSE_SLOT_BINDING"},
                {
                    "kind": "TIME.ESTIMATED_EFFECTIVE_DATE",
                    "message": "Effective date substituted by publication date.",
                    "source": "2025/78",
                    "detail": {"step": "publication_date"},
                },
                {
                    "kind": "APPLY.UNCOVERED_BODY_RECOVERY",
                    "message": "Uncovered body text was preserved as recovery evidence.",
                    "source": "2025/79",
                    "detail": {"recovery_rule": "preserve_uncovered_body"},
                },
            ],
            "failed_ops": [{"reason_code": "unsupported"}],
            "strict_fail_reasons": [
                "source_incomplete",
                "TIME.ESTIMATED_EFFECTIVE_DATE",
                "APPLY.UNCOVERED_BODY_RECOVERY",
            ],
        }
    )

    assert report["jurisdiction"] == "fi"
    assert report["report_kind"] == "finland_strict_report"
    assert report["replay_claims"] is False
    assert report["canonical_effect_claims"] is True
    assert report["agreement_claims"] is False
    assert report["summary"]["canonical_op_count"] == 2
    assert report["summary"]["source_pathology_count"] == 1
    assert report["summary"]["source_pathology_kind_counts"] == {"DESTRUCTIVE_SHAPE_LOSS_RISK": 1}
    assert report["summary"]["source_pathology_frontier_work_item_count"] == 1
    assert report["summary"]["sparse_slot_candidate_set_certificate_count"] == 1
    assert report["summary"]["agreement_residual_count"] == 1
    assert report["summary"]["agreement_residual_family_counts"] == {
        "non_commensurable_surface": 1
    }
    assert report["summary"]["agreement_materialization_kind"] == "legal_text_state"
    assert (
        report["summary"]["agreement_comparison_materialization_kind"]
        == "official_consolidation_view"
    )
    assert report["summary"]["mutation_boundary_proof_count"] == 1
    assert report["summary"]["source_completeness_status_count"] == 1
    assert report["summary"]["source_completeness"] == {
        "chain_length": 3,
        "source_available": 2,
        "dates_available": 1,
        "missing_sources": 1,
        "missing_dates": 2,
    }
    assert report["summary"]["temporal_resolution_evidence_count"] == 1
    assert report["summary"]["recovery_execution_authorization_count"] == 1
    assert report["summary"]["source_pathology_frontier_source_witness_digest_coverage_counts"] == {"preview_digest": 1}
    assert [row["surface"] for row in report["rows"]] == [
        "source_pathology",
        "source_pathology_execution_authorization",
        "source_pathology_frontier_work_item",
        "sparse_slot_candidate_set_certificate",
        "agreement_residual",
        "mutation_boundary_proof",
        "source_completeness_status",
        "temporal_resolution_evidence",
        "recovery_execution_authorization",
    ]
    rows_by_surface = {row["surface"]: row for row in report["rows"]}
    source_pathology = rows_by_surface["source_pathology"]
    assert source_pathology["replay_authorized"] is False
    assert source_pathology["affected_phase"] == "replay_apply"
    agreement = rows_by_surface["agreement_residual"]
    assert agreement["replay_authorized"] is False
    assert agreement["family"] == "non_commensurable_surface"
    temporal = rows_by_surface["temporal_resolution_evidence"]
    assert temporal["family"] == "temporal_recovery"
    assert temporal["temporal_resolution_status"] == "unknown_effective_date"
    assert temporal["strict_disposition"] == "block"
    assert temporal["source_locator"] == "2025/78"
    recovery = rows_by_surface["recovery_execution_authorization"]
    assert recovery["authorization_status"] == "strict_recovery_blocked"
    assert recovery["replay_authorized"] is False
    assert recovery["owner_phase"] == "replay_apply"
    assert recovery["finding_kind"] == "APPLY.UNCOVERED_BODY_RECOVERY"
    assert recovery["family"] == "uncovered_body_recovery"
    assert "recovery_projection_as_replay_authorization" in recovery["forbidden_shortcuts"]
    source_status = rows_by_surface["source_completeness_status"]
    assert source_status["status"] == "incomplete"
    assert source_status["counts"]["missing_sources"] == 1
    assert source_status["counts"]["missing_dates"] == 2
    assert "source_completeness_status_as_replay_authorization" in source_status["forbidden_shortcuts"]
    assert "temporal_resolution_evidence_as_unconditional_commencement_proof" in report["forbidden_shortcuts"]
    assert "recovery_projection_as_replay_authorization" in report["forbidden_shortcuts"]
    assert "source_completeness_status_as_replay_authorization" in report["forbidden_shortcuts"]


def test_source_completeness_status_row_records_counts_without_authority() -> None:
    incomplete = source_completeness_status_row(
        {
            "statute_id": "2001/1234",
            "source_completeness": {
                "chain_length": 4,
                "source_available": 3,
                "dates_available": 4,
            },
        }
    )
    complete = source_completeness_status_row(
        {
            "statute_id": "2001/1234",
            "source_completeness": {
                "chain_length": 2,
                "source_available": 2,
                "dates_available": 2,
            },
        }
    )

    assert incomplete["status"] == "incomplete"
    assert incomplete["counts"]["missing_sources"] == 1
    assert incomplete["counts"]["missing_dates"] == 0
    assert complete["status"] == "complete"
    assert source_completeness_status_row({"source_completeness": {}}) == {}


def test_finland_strict_report_ownership_closure_can_close_declared_slice() -> None:
    candidate_set_kinds = (
        "fi_strict_report_visible_operation_rows",
        "fi_strict_report_source_lineage_units",
        "fi_strict_report_source_unit_enumeration",
        "fi_strict_report_operation_cue_coverage",
    )
    candidate_sets = [
        CandidateSetCertificate(
            scope_id=f"fi:2001/1234:{kind}",
            candidate_set_kind=kind,
            phase="strict_report_projection",
            rule_id=f"{kind}_complete",
            reason="declared test slice is fully enumerated",
            completeness_status=CANDIDATE_SET_COMPLETE,
            candidate_count=1,
            candidate_ids=(f"{kind}:candidate",),
            missing_candidate_count=0,
            blocker_counts={},
            blocker_families=(),
            next_promotion_allowed=False,
            next_promotion_requires=("execution_authorization",),
        ).to_dict()
        for kind in candidate_set_kinds
    ]

    certificate = finland_strict_report_ownership_closure_certificate(
        {
            "statute_id": "2001/1234",
            "profile": "strict",
            "canonical_ops": [],
            "failed_ops": [],
            "projection_rows": [],
            "source_pathologies": [],
            "strict_fail_reasons": [],
            "strict_report_candidate_set_certificates": candidate_sets,
            "strict_report_candidate_set_execution_authorizations": [],
            "mutation_boundary_proofs": [],
        }
    )

    assert certificate["closed"] is True
    assert certificate["closure_status"] == "closed"
    assert certificate["failed_gates"] == []
    assert set(certificate["unowned_counts"].values()) == {0}
    assert certificate["detail"]["missing_required_certificates"] == []
    assert certificate["detail"]["closure_dimensions"] == [
        "visible_operation_rows",
        "source_lineage_units",
        "source_unit_enumeration",
        "operation_cue_coverage",
        "failed_operations",
        "strict_fail_reasons",
        "mutation_boundary_proofs",
    ]
    assert "source_unit_enumeration_closure" not in certificate["detail"]["does_not_claim"]
    assert "operation_candidate_coverage_closure" not in certificate["detail"]["does_not_claim"]
    assert "full_finland_corpus_closure" in certificate["detail"]["does_not_claim"]
    assert "replay_authorization" in certificate["detail"]["does_not_claim"]


def test_temporal_resolution_evidence_rows_project_finland_time_findings() -> None:
    rows = temporal_resolution_evidence_rows_from_projection_rows(
        (
            {
                "kind": "TIME.CONTINGENT_EFFECTIVE_DATE",
                "message": "Effective date is contingent.",
                "source": "2020/1",
                "detail": {"step": "contingent_text"},
            },
            {
                "kind": "TIME.ESTIMATED_EFFECTIVE_DATE",
                "message": "Effective date estimated.",
                "source": "2021/2",
                "detail": {"step": "text_regex"},
            },
            {"kind": "ELAB.SPARSE_SLOT_BINDING"},
        ),
        strict_fail_reasons=("TIME.CONTINGENT_EFFECTIVE_DATE",),
    )

    assert len(rows) == 2
    assert rows[0]["rule_id"] == "fi_time_contingent_effective_date"
    assert rows[0]["temporal_resolution_status"] == "unresolved_contingent"
    assert rows[0]["strict_disposition"] == "block"
    assert rows[0]["step"] == "contingent_text"
    assert rows[1]["rule_id"] == "fi_time_estimated_effective_date"
    assert rows[1]["temporal_resolution_status"] == "unknown_effective_date"
    assert rows[1]["strict_disposition"] == "record"


def test_recovery_execution_authorization_rows_project_finland_recoveries() -> None:
    rows = recovery_execution_authorization_rows_from_projection_rows(
        (
            {
                "kind": "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION",
                "message": "Anchor was resolved from source context.",
                "source": "2020/1",
                "detail": {"target": "section:3"},
            },
            {
                "kind": "APPLY.STRICT_REJECTED_UNCOVERED_BODY",
                "message": "Uncovered body recovery was rejected in strict mode.",
                "source": "2021/2",
            },
            {"kind": "TIME.ESTIMATED_EFFECTIVE_DATE"},
        ),
        strict_fail_reasons=("APPLY.STRICT_REJECTED_UNCOVERED_BODY",),
        statute_id="2001/1234",
    )

    assert len(rows) == 2
    assert rows[0]["authorization_status"] == "recovery_projection_not_replay_authority"
    assert rows[0]["owner_phase"] == "canonical_op_compilation"
    assert rows[0]["strict_disposition"] == "record"
    assert rows[0]["replay_authorized"] is False
    assert rows[0]["detail"]["family"] == "context_dependent_anchor_resolution"
    assert rows[0]["source_artifact_id"] == "2020/1"
    assert rows[1]["authorization_status"] == "strict_recovery_blocked"
    assert rows[1]["owner_phase"] == "replay_apply"
    assert rows[1]["strict_disposition"] == "block"
    assert "mutation_boundary_proof_before_replay_promotion" in rows[1]["required_proofs"]
    assert "recovery_finding_as_mutation_boundary_proof" in rows[1]["forbidden_shortcuts"]


def test_finlex_editorial_witness_residuals_classify_agreement_and_disagreement() -> None:
    rows = finlex_editorial_witness_agreement_residual_rows(
        (
            {
                "kind": "editorial_witness_confirmed",
                "slot_address": "section:3/subsection:1/paragraph:2",
                "amendment_id": "2021/1030",
            },
            {
                "kind": "editorial_witness_disagrees",
                "slot_address": "section:3/subsection:1/paragraph:2",
                "amendment_id": "2020/999",
                "timeline_terminator": "2021/1030",
                "severity": "REQUIRES_TRIAGE",
            },
            {
                "kind": "editorial_witness_unresolved",
                "slot_address": "section:3/subsection:1/paragraph:2",
                "amendment_id": "2021/1030",
                "timeline_terminator": None,
            },
        ),
        statute_id="2013/331",
    )

    assert [row["status"] for row in rows] == ["agrees", "residual", "residual"]
    assert [row["family"] for row in rows] == ["agreement", "unknown", "source_footing_gap"]
    assert rows[0]["replay_count"] == 1
    assert rows[0]["oracle_count"] == 1
    assert rows[1]["missing_proofs"] == [
        "manual_editorial_witness_triage",
        "timeline_terminator_source_review",
    ]
    assert rows[2]["rule_id"] == "fi_finlex_inline_repeal_stub_unresolved"
    assert all("finlex_oracle_as_source_truth" in row["forbidden_shortcuts"] for row in rows)


def test_source_adjudication_noncommensurable_reason_projects_residual() -> None:
    rows = source_adjudication_agreement_residual_rows(
        {
            "statute_id": "2001/1234",
            "replay_mode": "legal_pit",
            "cutoff_date": "2024-01-01",
            "oracle_version_amendment_id": "2024/1",
            "html_noncommensurable_reason": "oracle_extra_scoped_labels:chapter:15/section:1",
        }
    )

    assert len(rows) == 1
    residual = rows[0]
    assert residual["family"] == "non_commensurable_surface"
    assert residual["status"] == "residual"
    assert residual["agreement_surface"] == "finlex_html_oracle_compare"
    assert residual["missing_proofs"] == ["compare_projection_review"]
    assert residual["detail"]["html_noncommensurable_reason"] == ("oracle_extra_scoped_labels:chapter:15/section:1")


def test_source_adjudication_lineage_projects_source_witnesses() -> None:
    rows = source_adjudication_lineage_source_witness_rows(
        {
            "statute_id": "2001/1234",
            "lineage": [
                {
                    "sequence": 1,
                    "statute_id": "2020/1",
                    "title": "Test amendment",
                    "effective_date": "2020-02-01",
                    "issue_date": "2020-01-01",
                    "sort_mode": "legal_pit",
                    "included": True,
                    "selection_basis": "oracle_editorial_repeal_stub_override",
                }
            ],
        }
    )

    assert len(rows) == 1
    witness = rows[0]
    assert witness["source_role"] == "finland_source_lineage_amendment"
    assert witness["artifact_id"] == "2020/1"
    assert witness["source_unit_id"] == "2020/1"
    assert witness["source_lane"] == "finland_source_adjudication_lineage"
    assert witness["version_id"] == "2020-02-01"
    assert witness["included"] is True
    assert witness["selection_basis"] == "oracle_editorial_repeal_stub_override"
    assert source_witness_digest_coverage(witness) == "preview_digest"
