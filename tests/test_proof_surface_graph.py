from __future__ import annotations

from lawvm.core.evidence_surface_report import EvidenceSurfaceReport
from lawvm.core.proof_surface_graph import graph_from_proof_surface
from lawvm.core.proof_surfaces import ProofSurface, ProofSurfaceRow
from lawvm.core.provenance_graph import ArtifactRef


def test_proof_surface_rows_project_to_graph_observations_without_authority() -> None:
    surface = ProofSurface(
        surface_id="fi:proof:demo",
        surface_kind="finland_recovery_findings",
        jurisdiction="fi",
        source_bundle_hash="bundle-1",
        claim_flags={
            "replay_claims": False,
            "canonical_effect_claims": True,
            "candidate_effect_claims": False,
            "dry_run_claims": False,
            "agreement_claims": False,
        },
        rows=(
            ProofSurfaceRow(
                row_id="row-1",
                subject_id="fi:123/2024",
                row_kind="recovery_authorization",
                status="blocked",
                source_refs=("source-a",),
                authorization_ref="auth-1",
                detail={"owner_phase": "typed_elaboration"},
            ),
        ),
    )

    graph, assertion_index = graph_from_proof_surface(surface)

    assert len(graph.nodes) == 1
    assert len(graph.edges) == 1
    assertion = next(iter(assertion_index.values()))
    assert assertion.kind == "lawvm.proof_surface.row.v0"
    assert assertion.layer == "facade_observation"
    assert assertion.value["row_id"] == "row-1"
    assert assertion.value["surface_claim_flags"] == {
        "replay_claims": False,
        "canonical_effect_claims": True,
        "candidate_effect_claims": False,
        "dry_run_claims": False,
        "agreement_claims": False,
    }
    assert assertion.value["replay_authorized"] is False
    assert assertion.value["read_model_only"] is True
    assert assertion.source_refs == ()
    assert assertion.dependency_refs[0].artifact_type == "proof_surface"
    assert "proof_surface_row_as_replay_authorization" in assertion.value[
        "forbidden_shortcuts"
    ]
    assert graph.edges[0].edge_type == "derives_projection"
    assert graph.edges[0].payload["projection_claim"] == (
        "read_model_only_not_replay_authority"
    )


def test_evidence_report_projects_through_proof_surface_graph_path() -> None:
    report = EvidenceSurfaceReport(
        jurisdiction="fi",
        report_kind="frontend_phase_surface",
        schema="lawvm.frontend_phase_surface.v1",
        truth_claim="frontend phase diagnostics",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=True,
        rows=(
            {
                "surface": "frontend_phase_row",
                "row_id": "phase-row-1",
                "source_ref": "source-hash-1",
                "status": "lowered",
            },
        ),
    )
    surface_ref = ArtifactRef(
        artifact_type="evidence_surface_report",
        artifact_id="report-1",
        content_hash="report-1",
    )

    graph, assertion_index = graph_from_proof_surface(
        report,
        surface_ref=surface_ref,
        source_bundle_hash="bundle-2",
    )

    assertion = next(iter(assertion_index.values()))
    assert len(graph.nodes) == 1
    assert assertion.scope["surface_kind"] == "frontend_phase_surface"
    assert assertion.scope["source_bundle_hash"] == "bundle-2"
    assert assertion.value["row_kind"] == "frontend_phase_row"
    assert assertion.value["surface_claim_flags"]["agreement_claims"] is True
    assert assertion.value["replay_authorized"] is False
    assert assertion.value["source_refs"] == ["source-hash-1"]
    assert assertion.dependency_refs == (surface_ref,)
    assert graph.edges[0].dst_node_id == "report-1"
