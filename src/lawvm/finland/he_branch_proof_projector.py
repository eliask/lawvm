"""Finland government-proposal (HE) branch proof-surface projections.

Report/read-model adapters only; no replay authorization semantics.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping

from lawvm.core.branch_authority import (
    UNKNOWN_STATUS,
    BranchEdgeKind,
    WOULD_AMEND_EDGE,
    WOULD_INSERT_EDGE,
    WOULD_REPEAL_EDGE,
    WOULD_REPLACE_EDGE,
    BranchGraphEdge,
    LegalBranch,
    PROPOSAL_AUTHORITY,
)
from lawvm.core.branch_projection import BranchImpactProjection, branch_impact_projection_from_edges
from lawvm.core.evidence_surface_report import EvidenceSurfaceReport
from lawvm.core.execution_authorization import ExecutionAuthorization
from lawvm.finland.proof_surface_row_helpers import field as _field

if TYPE_CHECKING:
    from lawvm.finland.he_branch_parser import HEParsedBranch

_HE_BRANCH_FORBIDDEN_SHORTCUTS: tuple[str, ...] = (
    "he_branch_op_as_enacted_operation",
    "he_branch_parse_success_as_replay_authorization",
    "he_branch_target_resolution_as_target_hijack",
    "he_branch_projection_as_current_law",
)
_HE_BRANCH_REQUIRED_PROOFS: tuple[str, ...] = (
    "enacted_source_identity_proof",
    "proposal_enactment_proof",
    "target_identity_proof_against_enacted_state",
    "mutation_boundary_proof_before_replay_promotion",
)


def finland_he_branch_evidence_surface(
    parsed: HEParsedBranch | Mapping[str, Any],
) -> dict[str, Any]:
    """Wrap a parsed Finland HE branch in a passive proof-surface envelope.

    HE branches are future-law/proposal diagnostics.  This surface makes typed
    proposal parsing visible without claiming enacted-law authority, canonical
    effects, dry-run authority, or agreement with an oracle.
    """

    proposed_ops = tuple(_field(parsed, "proposed_ops", ()) or ())
    parse_findings = tuple(_field(parsed, "parse_findings", ()) or ())
    target_statute_ids = tuple(str(item) for item in (_field(parsed, "target_statute_ids", ()) or ()) if str(item))
    branch_projection_row = _he_branch_impact_projection_row(
        parsed=parsed,
        proposed_ops=proposed_ops,
    )
    rows = (
        *(_he_branch_proposed_op_row(op) for op in proposed_ops),
        *(_he_branch_finding_row(finding) for finding in parse_findings),
        *((branch_projection_row,) if branch_projection_row is not None else ()),
    )
    parse_status = _enum_text(_field(parsed, "parse_status", ""))
    summary = {
        "proposed_op_count": len(proposed_ops),
        "target_statute_count": len(target_statute_ids),
        "branch_impact_projection_count": 1 if branch_projection_row is not None else 0,
        "branch_impact_row_count": (
            _nonnegative_int(branch_projection_row["branch_impact_row_count"])
            if branch_projection_row is not None
            else 0
        ),
        "parse_finding_count": len(parse_findings),
        "enactment_sections_found": _nonnegative_int(_field(parsed, "enactment_sections_found", 0)),
        "clauses_attempted": _nonnegative_int(_field(parsed, "clauses_attempted", 0)),
        "clauses_succeeded": _nonnegative_int(_field(parsed, "clauses_succeeded", 0)),
        "parse_status": parse_status,
        "proposal_relative_op_count": sum(
            1
            for op in proposed_ops
            if bool(_field(op, "is_proposal_relative", False))
            or _enum_text(_field(op, "target_resolution", "")) == "proposal_relative"
        ),
        "unresolved_target_finding_count": sum(
            1
            for finding in parse_findings
            if str(_field(finding, "rule_id", "")).startswith("HE_BRANCH.TARGET_")
        ),
    }
    voimaantulo = _field(parsed, "proposed_voimaantulo", None)
    return EvidenceSurfaceReport(
        jurisdiction="fi",
        report_kind="finland_he_branch",
        schema="lawvm.finland_he_branch.v1",
        truth_claim="finland_government_proposal_branch_parse_diagnostics",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        summary=summary,
        filters={
            "branch_id": str(_field(parsed, "branch_id", "")),
            "he_id": str(_field(parsed, "he_id", "")),
            "parse_status": parse_status,
        },
        filtered_summary=summary,
        rows=rows,
        rows_truncated=False,
        detail={
            "safe_default": "treat_he_branch_rows_as_future_law_diagnostics_not_current_law_authority",
            "forbidden_shortcuts": _HE_BRANCH_FORBIDDEN_SHORTCUTS,
            "included_surfaces": (
                "he_branch_proposed_op",
                "he_branch_target_resolution_finding",
                "he_branch_parse_finding",
                "he_branch_impact_projection",
            ),
            "target_statute_ids": target_statute_ids,
            "he_year": _nonnegative_int(_field(parsed, "he_year", 0)),
            "he_number": _nonnegative_int(_field(parsed, "he_number", 0)),
            "proposed_voimaantulo": str(voimaantulo) if voimaantulo is not None else "",
        },
    ).to_dict()


def _he_branch_impact_projection_row(
    *,
    parsed: Any,
    proposed_ops: tuple[Any, ...],
) -> dict[str, Any] | None:
    branch_id = str(_field(parsed, "branch_id", ""))
    if not branch_id:
        return None
    he_id = str(_field(parsed, "he_id", ""))
    projection = _he_branch_impact_projection(
        branch=LegalBranch(
            branch_id=branch_id,
            authority_layer=PROPOSAL_AUTHORITY,
            legal_status=UNKNOWN_STATUS,
            source_artifact_id=he_id,
            title=he_id,
        ),
        proposed_ops=proposed_ops,
    )
    return {
        "surface": "he_branch_impact_projection",
        "authority_status": "branch_projection_not_enacted_authority",
        "owner_phase": "branch_projection",
        "branch_id": branch_id,
        "source_he_id": he_id,
        "branch_impact_row_count": len(projection.rows),
        "projection": projection.to_dict(),
        "executable": False,
        "replay_authorized": False,
        "forbidden_shortcuts": list(_HE_BRANCH_FORBIDDEN_SHORTCUTS),
    }


def _he_branch_impact_projection(
    *,
    branch: LegalBranch,
    proposed_ops: tuple[Any, ...],
) -> BranchImpactProjection:
    edges = tuple(
        edge
        for op in proposed_ops
        if (edge := _he_branch_graph_edge(branch=branch, op=op)) is not None
    )
    return branch_impact_projection_from_edges(
        branch,
        edges,
        projection_status="diagnostic_only",
        message="Finland government-proposal branch impact projection is not enacted-law authority.",
    )


def _he_branch_graph_edge(*, branch: LegalBranch, op: Any) -> BranchGraphEdge | None:
    target_statute_id = str(_field(op, "target_statute_id", ""))
    if not target_statute_id:
        return None
    op_index = _nonnegative_int(_field(op, "op_index", 0))
    return BranchGraphEdge(
        branch_id=branch.branch_id,
        edge_kind=_he_branch_edge_kind(str(_field(op, "operation_kind", ""))),
        scenario_id=branch.scenario_id,
        source_artifact_id=str(_field(op, "source_he_id", branch.source_artifact_id)),
        source_statute_id=str(_field(op, "source_he_id", branch.source_artifact_id)),
        source_unit_id=f"proposed-op:{op_index}",
        target_statute_id=target_statute_id,
        target_address=str(_field(op, "target_provision_ref", "")),
        operation_id=f"{branch.branch_id}:proposed-op:{op_index}",
        authority_layer=branch.authority_layer,
        legal_status=branch.legal_status,
    )


def _he_branch_edge_kind(operation_kind: str) -> BranchEdgeKind:
    normalized = operation_kind.strip().lower()
    if normalized == "insert":
        return WOULD_INSERT_EDGE
    if normalized in {"replace", "amend", "change"}:
        return WOULD_REPLACE_EDGE
    if normalized in {"repeal", "omit", "delete"}:
        return WOULD_REPEAL_EDGE
    return WOULD_AMEND_EDGE


def _he_branch_proposed_op_row(op: Any) -> dict[str, Any]:
    authorization = ExecutionAuthorization(
        executable=False,
        replay_authorized=False,
        authorization_status="he_branch_proposal_not_replay_authority",
        authorization_rule_id="fi_he_branch_proposal_surface_only",
        owner_phase="surface_parse",
        strict_disposition="record",
        quirks_disposition="record",
        validator_status="not_validated_for_replay_promotion",
        required_proofs=_HE_BRANCH_REQUIRED_PROOFS,
        safe_default="record_as_future_law_diagnostic_without_replay_promotion",
        forbidden_shortcuts=_HE_BRANCH_FORBIDDEN_SHORTCUTS,
        detail={
            "branch_id": str(_field(op, "branch_id", "")),
            "source_he_id": str(_field(op, "source_he_id", "")),
            "op_index": _nonnegative_int(_field(op, "op_index", 0)),
        },
    )
    return {
        "surface": "he_branch_proposed_op",
        "authority_status": "proposed_branch_op_not_enacted_authority",
        "owner_phase": "surface_parse",
        "op_index": _nonnegative_int(_field(op, "op_index", 0)),
        "operation_kind": str(_field(op, "operation_kind", "")),
        "target_provision_ref": str(_field(op, "target_provision_ref", "")),
        "target_statute_id": str(_field(op, "target_statute_id", "")),
        "target_resolution": _enum_text(_field(op, "target_resolution", "")),
        "is_proposal_relative": bool(_field(op, "is_proposal_relative", False)),
        "parse_confidence": float(_field(op, "parse_confidence", 0.0) or 0.0),
        "payload_summary": str(_field(op, "payload_summary", "")),
        "source_he_id": str(_field(op, "source_he_id", "")),
        "branch_id": str(_field(op, "branch_id", "")),
        "source_span_text": str(_field(op, "source_span_text", "")),
        "source_span_preamble": str(_field(op, "source_span_preamble", "")),
        "execution_authorization": authorization.to_dict(),
        "forbidden_shortcuts": list(_HE_BRANCH_FORBIDDEN_SHORTCUTS),
    }


def _he_branch_finding_row(finding: Any) -> dict[str, Any]:
    rule_id = str(_field(finding, "rule_id", ""))
    target_ref = str(_field(finding, "target_provision_ref", ""))
    is_target_finding = bool(target_ref) or rule_id.startswith("HE_BRANCH.TARGET_")
    owner_phase = "target_resolution" if is_target_finding else str(_field(finding, "phase", "surface_parse"))
    surface = "he_branch_target_resolution_finding" if is_target_finding else "he_branch_parse_finding"
    authorization = ExecutionAuthorization(
        executable=False,
        replay_authorized=False,
        authorization_status="he_branch_finding_not_replay_authority",
        authorization_rule_id="fi_he_branch_finding_surface_only",
        owner_phase=owner_phase,
        strict_disposition=str(_field(finding, "strict_disposition", "record") or "record"),
        quirks_disposition="record",
        validator_status="not_validated_for_replay_promotion",
        required_proofs=_HE_BRANCH_REQUIRED_PROOFS,
        safe_default="record_finding_and_preserve_uncertainty",
        forbidden_shortcuts=_HE_BRANCH_FORBIDDEN_SHORTCUTS,
        detail={
            "rule_id": rule_id,
            "op_index": _nonnegative_int(_field(finding, "op_index", 0)),
            "target_provision_ref": target_ref,
        },
    )
    return {
        "surface": surface,
        "finding_status": "recorded",
        "owner_phase": owner_phase,
        "rule_id": rule_id,
        "op_index": _nonnegative_int(_field(finding, "op_index", 0)),
        "reason": str(_field(finding, "reason", "")),
        "detail": str(_field(finding, "detail", "")),
        "family": str(_field(finding, "family", "target_resolution" if is_target_finding else "source_pathology")),
        "strict_disposition": str(_field(finding, "strict_disposition", "record") or "record"),
        "target_provision_ref": target_ref,
        "target_statute_id": str(_field(finding, "target_statute_id", "")),
        "is_proposal_relative": bool(_field(finding, "is_proposal_relative", False)),
        "clause_text": str(_field(finding, "clause_text", "")),
        "execution_authorization": authorization.to_dict(),
        "forbidden_shortcuts": list(_HE_BRANCH_FORBIDDEN_SHORTCUTS),
    }


def _enum_text(value: Any) -> str:
    enum_value = getattr(value, "value", value)
    return str(enum_value or "")


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0


__all__ = ["finland_he_branch_evidence_surface"]
