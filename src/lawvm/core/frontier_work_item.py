"""Shared non-executable frontier work-item projection contract."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, Mapping, cast

from lawvm.core.evidence_surface_report import EvidenceSurfaceReport
from lawvm.core.frozen_values import freeze_mapping


_FRONTIER_WORK_ITEM_REPORT_FORBIDDEN_SHORTCUTS: tuple[str, ...] = (
    "frontier_work_item_as_replay_authorization",
    "frontier_work_item_as_canonical_operation",
    "frontier_work_item_as_mutation_boundary_proof",
)

_FRONTIER_CLAIM_CLOSURE_FORBIDDEN_SHORTCUTS: tuple[str, ...] = (
    "frontier_claim_closure_as_replay_authorization",
    "evidence_policy_satisfaction_as_mutation_boundary_proof",
    "claim_kind_match_as_target_resolution_proof",
)

_FRONTIER_CLAIM_TEMPLATE_FORBIDDEN_SHORTCUTS: tuple[str, ...] = (
    "frontier_claim_template_as_replay_authorization",
    "frontier_claim_template_as_canonical_operation",
    "frontier_claim_template_as_validator_result",
)


@dataclass(frozen=True, slots=True)
class FrontierWorkItem:
    """Describe useful non-executable work without promoting it to replay."""

    work_item_id: str
    jurisdiction: str
    source_artifact_id: str
    source_unit_id: str
    owner_phase: str
    frontier_family: str
    frontier_status: str
    required_claim_kind: str
    safe_default: str
    source_witness: Mapping[str, Any] = field(default_factory=dict)
    target_witness: Mapping[str, Any] = field(default_factory=dict)
    compare_witness: Mapping[str, Any] = field(default_factory=dict)
    candidate_operation_family: str = ""
    candidate_targets: tuple[str, ...] = ()
    guidance_refs: tuple[str, ...] = ()
    required_validator_checks: tuple[str, ...] = ()
    required_proofs: tuple[str, ...] = ()
    forbidden_shortcuts: tuple[str, ...] = ()
    executable: bool = False
    replay_authorized: bool = False
    authorization_status: str = ""
    suggested_claim_template_status: str = ""
    suggested_claim_template: Mapping[str, Any] = field(default_factory=dict)
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in (
            "work_item_id",
            "jurisdiction",
            "source_artifact_id",
            "source_unit_id",
            "owner_phase",
            "frontier_family",
            "frontier_status",
            "required_claim_kind",
            "safe_default",
            "candidate_operation_family",
            "authorization_status",
            "suggested_claim_template_status",
        ):
            object.__setattr__(
                self,
                field_name,
                str(getattr(self, field_name) or ""),
            )
        object.__setattr__(
            self,
            "candidate_targets",
            tuple(str(item) for item in self.candidate_targets if str(item)),
        )
        object.__setattr__(
            self,
            "guidance_refs",
            tuple(str(item) for item in self.guidance_refs if str(item)),
        )
        object.__setattr__(
            self,
            "required_validator_checks",
            tuple(str(item) for item in self.required_validator_checks if str(item)),
        )
        object.__setattr__(
            self,
            "required_proofs",
            tuple(str(item) for item in self.required_proofs if str(item)),
        )
        object.__setattr__(
            self,
            "forbidden_shortcuts",
            tuple(str(item) for item in self.forbidden_shortcuts if str(item)),
        )
        if not isinstance(self.source_witness, Mapping):
            raise ValueError("FrontierWorkItem.source_witness must be a mapping")
        if not isinstance(self.target_witness, Mapping):
            raise ValueError("FrontierWorkItem.target_witness must be a mapping")
        if not isinstance(self.compare_witness, Mapping):
            raise ValueError("FrontierWorkItem.compare_witness must be a mapping")
        if not isinstance(self.suggested_claim_template, Mapping):
            raise ValueError("FrontierWorkItem.suggested_claim_template must be a mapping")
        if not isinstance(self.detail, Mapping):
            raise ValueError("FrontierWorkItem.detail must be a mapping")
        object.__setattr__(self, "source_witness", freeze_mapping(self.source_witness))
        object.__setattr__(self, "target_witness", freeze_mapping(self.target_witness))
        object.__setattr__(
            self,
            "compare_witness",
            freeze_mapping(self.compare_witness),
        )
        object.__setattr__(
            self,
            "suggested_claim_template",
            freeze_mapping(self.suggested_claim_template),
        )
        object.__setattr__(self, "detail", freeze_mapping(self.detail))
        issues = validate_frontier_work_item(self.to_dict())
        if issues:
            raise ValueError("; ".join(issues))

    def to_dict(self) -> dict[str, Any]:
        return {
            "work_item_id": self.work_item_id,
            "jurisdiction": self.jurisdiction,
            "source_artifact_id": self.source_artifact_id,
            "source_unit_id": self.source_unit_id,
            "source_witness": _plain_jsonable(self.source_witness),
            "target_witness": _plain_jsonable(self.target_witness),
            "compare_witness": _plain_jsonable(self.compare_witness),
            "owner_phase": self.owner_phase,
            "frontier_family": self.frontier_family,
            "frontier_status": self.frontier_status,
            "candidate_operation_family": self.candidate_operation_family,
            "candidate_targets": list(self.candidate_targets),
            "guidance_refs": list(self.guidance_refs),
            "required_claim_kind": self.required_claim_kind,
            "required_validator_checks": list(self.required_validator_checks),
            "required_proofs": list(self.required_proofs),
            "safe_default": self.safe_default,
            "forbidden_shortcuts": list(self.forbidden_shortcuts),
            "executable": self.executable,
            "replay_authorized": self.replay_authorized,
            "authorization_status": self.authorization_status,
            "suggested_claim_template_status": self.suggested_claim_template_status,
            "suggested_claim_template": _plain_jsonable(self.suggested_claim_template),
            "detail": _plain_jsonable(self.detail),
        }


def validate_frontier_work_item(row: Mapping[str, Any]) -> tuple[str, ...]:
    """Validate the shared non-executable frontier work-item projection."""
    issues: list[str] = []
    for key in (
        "work_item_id",
        "jurisdiction",
        "source_artifact_id",
        "source_unit_id",
        "owner_phase",
        "frontier_family",
        "frontier_status",
        "required_claim_kind",
        "safe_default",
    ):
        value = row.get(key)
        if not isinstance(value, str) or not value:
            issues.append(f"{key} is required")
    if row.get("executable") is not False:
        issues.append("frontier work items must be non-executable")
    if row.get("replay_authorized") is not False:
        issues.append("frontier work items must not be replay-authorized")
    for key in ("source_witness", "target_witness", "compare_witness"):
        if not isinstance(row.get(key, {}), Mapping):
            issues.append(f"{key} must be a mapping")
    if not isinstance(row.get("suggested_claim_template", {}), Mapping):
        issues.append("suggested_claim_template must be a mapping")
    template_status = str(row.get("suggested_claim_template_status") or "")
    template = row.get("suggested_claim_template") or {}
    if template_status == "available" and not template:
        issues.append("available suggested_claim_template_status requires suggested_claim_template")
    if isinstance(template, Mapping):
        if template.get("executable") is True:
            issues.append("suggested_claim_template must be non-executable")
        if template.get("replay_authorized") is True:
            issues.append("suggested_claim_template must not authorize replay")
    for key in (
        "candidate_targets",
        "guidance_refs",
        "required_validator_checks",
        "required_proofs",
        "forbidden_shortcuts",
    ):
        if not isinstance(row.get(key, ()), (list, tuple)):
            issues.append(f"{key} must be a sequence")
    if not row.get("authorization_status"):
        issues.append("authorization_status is required")
    if not row.get("required_proofs"):
        issues.append("required_proofs is required")
    if not row.get("forbidden_shortcuts"):
        issues.append("forbidden_shortcuts is required")
    if not isinstance(row.get("detail", {}), Mapping):
        issues.append("detail must be a mapping")
    return tuple(issues)


def frontier_work_item_claim_template(
    work_item: FrontierWorkItem | Mapping[str, Any],
) -> dict[str, Any]:
    """Return a passive manual-claim template for a frontier work item.

    The template is review scaffolding only.  It names the claim kind, target
    and value fields, witnesses, validator checks, and proof obligations that a
    future claim must satisfy, without validating the claim or authorizing
    replay.
    """

    row = _frontier_mapping(work_item)
    claim_kind = str(row.get("required_claim_kind") or "")
    spec = _registered_claim_kind_spec(claim_kind)
    target_fields = tuple(getattr(spec, "target_fields", ()) or ())
    value_fields = tuple(getattr(spec, "value_fields", ()) or ())
    layer = str(getattr(spec, "layer", "") or "")
    description = str(getattr(spec, "description", "") or "")
    semantic = bool(getattr(spec, "is_semantic_compilation_claim", False))
    forbidden_shortcuts = tuple(
        dict.fromkeys(
            (
                *tuple(str(item) for item in _sequence(row.get("forbidden_shortcuts"))),
                *_FRONTIER_CLAIM_TEMPLATE_FORBIDDEN_SHORTCUTS,
            )
        )
    )
    return {
        "schema": "lawvm.frontier_work_item_claim_template.v1",
        "template_id": f"{row.get('work_item_id')}:claim-template",
        "frontier_ref": str(row.get("work_item_id") or ""),
        "claim_target_seed": {"frontier_ref": str(row.get("work_item_id") or "")},
        "jurisdiction": str(row.get("jurisdiction") or ""),
        "claim_kind": claim_kind,
        "claim_layer": layer,
        "claim_description": description,
        "registered_claim_kind": spec is not None,
        "semantic_compilation_claim": semantic,
        "source_witness": _plain_jsonable(row.get("source_witness") or {}),
        "target_witness": _plain_jsonable(row.get("target_witness") or {}),
        "compare_witness": _plain_jsonable(row.get("compare_witness") or {}),
        "candidate_operation_family": str(row.get("candidate_operation_family") or ""),
        "candidate_targets": list(_sequence(row.get("candidate_targets"))),
        "required_target_fields": list(target_fields),
        "required_value_fields": list(value_fields),
        "required_validator_checks": list(_sequence(row.get("required_validator_checks"))),
        "required_proofs": list(_sequence(row.get("required_proofs"))),
        "safe_default": str(row.get("safe_default") or ""),
        "forbidden_shortcuts": list(forbidden_shortcuts),
        "executable": False,
        "replay_authorized": False,
        "authorization_status": "claim_template_not_replay_authority",
        "detail": {
            "frontier_family": str(row.get("frontier_family") or ""),
            "frontier_status": str(row.get("frontier_status") or ""),
            "owner_phase": str(row.get("owner_phase") or ""),
            "source_artifact_id": str(row.get("source_artifact_id") or ""),
            "source_unit_id": str(row.get("source_unit_id") or ""),
        },
    }


def frontier_work_item_claim_template_status(
    template: Mapping[str, Any],
) -> str:
    """Return availability status for a passive frontier claim template."""

    if not template:
        return "not_available"
    if template.get("registered_claim_kind") is True:
        return "available"
    return "unregistered_claim_kind"


def frontier_work_item_with_claim_template(
    work_item: FrontierWorkItem | Mapping[str, Any],
) -> FrontierWorkItem:
    """Attach a passive claim template to a frontier work item."""

    row = dict(_frontier_mapping(work_item))
    template = frontier_work_item_claim_template(row)
    status = frontier_work_item_claim_template_status(template)
    row["suggested_claim_template_status"] = status
    row["suggested_claim_template"] = template
    field_names = {item.name for item in fields(FrontierWorkItem)}
    return FrontierWorkItem(
        **{key: value for key, value in row.items() if key in field_names}
    )


def frontier_work_item_evidence_report(
    work_items: (
        FrontierWorkItem
        | Mapping[str, Any]
        | tuple[FrontierWorkItem | Mapping[str, Any], ...]
    ),
    *,
    jurisdiction: str = "",
    report_kind: str = "frontier_work_item",
) -> EvidenceSurfaceReport:
    """Project non-executable frontier work items into a shared report envelope.

    This adapter is deliberately passive.  It gives frontends a common
    report/read-model shape for manual or blocked work without turning those
    rows into operations, dry-runs, or replay authority.
    """

    rows = tuple(_frontier_mapping(row) for row in _frontier_sequence(work_items))
    report_rows = tuple(_frontier_report_row(row) for row in rows)
    family_counts = _counts(str(row.get("frontier_family") or "") for row in rows)
    status_counts = _counts(str(row.get("frontier_status") or "") for row in rows)
    owner_phase_counts = _counts(str(row.get("owner_phase") or "") for row in rows)
    authorization_status_counts = _counts(
        str(row.get("authorization_status") or "")
        for row in rows
    )
    required_claim_kind_counts = _counts(
        str(row.get("required_claim_kind") or "")
        for row in rows
    )
    candidate_operation_family_counts = _counts(
        str(row.get("candidate_operation_family") or "__blank__")
        for row in rows
    )
    required_validator_check_counts = _counts(
        str(check)
        for row in rows
        for check in _sequence(row.get("required_validator_checks"))
    )
    suggested_claim_template_status_counts = _counts(
        str(row.get("suggested_claim_template_status") or "__none__")
        for row in rows
    )
    suggested_claim_template_kind_counts = _counts(
        str(_template_claim_kind(row) or "__none__")
        for row in rows
    )
    summary = {
        "frontier_work_item_count": len(rows),
        "frontier_family_counts": family_counts,
        "frontier_status_counts": status_counts,
        "owner_phase_counts": owner_phase_counts,
        "authorization_status_counts": authorization_status_counts,
        "required_claim_kind_counts": required_claim_kind_counts,
        "candidate_operation_family_counts": candidate_operation_family_counts,
        "required_validator_check_counts": required_validator_check_counts,
        "suggested_claim_template_status_counts": suggested_claim_template_status_counts,
        "suggested_claim_template_kind_counts": suggested_claim_template_kind_counts,
        "replay_authorized_count": 0,
        "executable_count": 0,
        "claim_flags": {
            "replay_claims": False,
            "canonical_effect_claims": False,
            "candidate_effect_claims": False,
            "dry_run_claims": False,
            "agreement_claims": False,
        },
    }
    return EvidenceSurfaceReport(
        jurisdiction=jurisdiction or _report_jurisdiction(rows),
        report_kind=report_kind,
        schema="lawvm.frontier_work_item_report.v1",
        truth_claim="non-executable frontier work item projections",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        summary=summary,
        filters={"report_kind": report_kind},
        filtered_summary=summary,
        rows=report_rows,
        rows_truncated=False,
        detail={
            "safe_default": "treat_frontier_work_items_as_blocked_non_executable_work",
            "forbidden_shortcuts": _FRONTIER_WORK_ITEM_REPORT_FORBIDDEN_SHORTCUTS,
            "included_surfaces": ("frontier_work_item",),
        },
    )


def frontier_work_item_claim_closure_report(
    work_item: FrontierWorkItem | Mapping[str, Any],
    *,
    assertion: Any,
    authorization_result: Any,
    jurisdiction: str = "",
    report_kind: str = "frontier_work_item_claim_closure",
) -> EvidenceSurfaceReport:
    """Report whether one authorized claim matches one frontier item.

    This is a passive closure/read-model bridge. It proves only that an
    assertion and evidence-policy result line up with a frontier work item; it
    never grants replay authority or lowers the claim into an operation.
    """

    frontier = _frontier_mapping(work_item)
    claim = _claim_assertion_mapping(assertion)
    authorization = _authorization_result_mapping(authorization_result)
    row = _frontier_claim_closure_row(
        frontier=frontier,
        claim=claim,
        authorization=authorization,
    )
    closure_status = str(row.get("closure_status") or "")
    summary = {
        "frontier_claim_closure_count": 1,
        "closure_status_counts": _counts((closure_status,)),
        "policy_authorized_count": 1 if row["policy_authorized"] else 0,
        "claim_kind_match_count": 1 if row["claim_kind_matches"] else 0,
        "frontier_ref_match_count": 1 if row["frontier_ref_matches"] else 0,
        "authorization_subject_match_count": 1 if row["authorization_subject_matches"] else 0,
        "phase_gate_required_count": 1 if row["phase_gate_required"] else 0,
        "replay_authorized_count": 0,
        "executable_count": 0,
        "claim_flags": {
            "replay_claims": False,
            "canonical_effect_claims": False,
            "candidate_effect_claims": False,
            "dry_run_claims": False,
            "agreement_claims": False,
        },
    }
    return EvidenceSurfaceReport(
        jurisdiction=jurisdiction or str(frontier.get("jurisdiction") or claim.get("jurisdiction") or ""),
        report_kind=report_kind,
        schema="lawvm.frontier_work_item_claim_closure_report.v1",
        truth_claim="manual-claim authorization matched to frontier work item",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        summary=summary,
        filters={"report_kind": report_kind},
        filtered_summary=summary,
        rows=(row,),
        rows_truncated=False,
        detail={
            "safe_default": "treat_frontier_claim_closure_as_phase_gate_evidence_not_replay_authority",
            "forbidden_shortcuts": _FRONTIER_CLAIM_CLOSURE_FORBIDDEN_SHORTCUTS,
            "included_surfaces": ("frontier_work_item_claim_closure",),
        },
    )


def _frontier_claim_closure_row(
    *,
    frontier: Mapping[str, Any],
    claim: Mapping[str, Any],
    authorization: Mapping[str, Any],
) -> Mapping[str, Any]:
    frontier_ref = str(frontier.get("work_item_id") or "")
    required_claim_kind = str(frontier.get("required_claim_kind") or "")
    claim_kind = str(claim.get("kind") or "")
    assertion_id = str(claim.get("assertion_id") or "")
    claim_frontier_ref = _claim_frontier_ref(claim)
    subject_id = str(authorization.get("subject_id") or "")
    policy_authorized = bool(authorization.get("authorized"))
    claim_kind_matches = bool(claim_kind and claim_kind == required_claim_kind)
    frontier_ref_matches = bool(claim_frontier_ref and claim_frontier_ref == frontier_ref)
    authorization_subject_matches = bool(subject_id and subject_id == assertion_id)
    closure_status = _frontier_claim_closure_status(
        policy_authorized=policy_authorized,
        claim_kind_matches=claim_kind_matches,
        frontier_ref_matches=frontier_ref_matches,
        authorization_subject_matches=authorization_subject_matches,
    )
    phase_gate_required = closure_status == "evidence_policy_satisfied_phase_gate_required"
    return {
        "surface": "frontier_work_item_claim_closure",
        "row_id": f"{frontier_ref}:{assertion_id}:claim-closure",
        "frontier_ref": frontier_ref,
        "assertion_id": assertion_id,
        "authorization_subject_id": subject_id,
        "required_claim_kind": required_claim_kind,
        "claim_kind": claim_kind,
        "claim_frontier_ref": claim_frontier_ref,
        "policy_id": str(authorization.get("policy_id") or ""),
        "policy_authorized": policy_authorized,
        "claim_kind_matches": claim_kind_matches,
        "frontier_ref_matches": frontier_ref_matches,
        "authorization_subject_matches": authorization_subject_matches,
        "closure_status": closure_status,
        "phase_gate_required": phase_gate_required,
        "executable": False,
        "replay_authorized": False,
        "required_proofs": list(_sequence(frontier.get("required_proofs"))) + [
            "phase_local_replay_authorization",
        ],
        "safe_default": "do_not_replay_manual_claim_from_closure_report",
        "forbidden_shortcuts": list(
            dict.fromkeys(
                (
                    *tuple(str(item) for item in _sequence(frontier.get("forbidden_shortcuts"))),
                    *_FRONTIER_CLAIM_CLOSURE_FORBIDDEN_SHORTCUTS,
                )
            )
        ),
        "detail": {
            "frontier_family": str(frontier.get("frontier_family") or ""),
            "frontier_status": str(frontier.get("frontier_status") or ""),
            "owner_phase": str(frontier.get("owner_phase") or ""),
            "evidence_bundle_hash": str(authorization.get("evidence_bundle_hash") or ""),
            "satisfied_clauses": list(_sequence(authorization.get("satisfied_clauses"))),
            "unsatisfied_clauses": list(_sequence(authorization.get("unsatisfied_clauses"))),
            "forbidden_present": list(_sequence(authorization.get("forbidden_present"))),
        },
    }


def _frontier_claim_closure_status(
    *,
    policy_authorized: bool,
    claim_kind_matches: bool,
    frontier_ref_matches: bool,
    authorization_subject_matches: bool,
) -> str:
    if not claim_kind_matches:
        return "claim_kind_mismatch"
    if not frontier_ref_matches:
        return "frontier_ref_mismatch"
    if not authorization_subject_matches:
        return "authorization_subject_mismatch"
    if not policy_authorized:
        return "evidence_policy_unsatisfied"
    return "evidence_policy_satisfied_phase_gate_required"


def _frontier_sequence(
    value: (
        FrontierWorkItem
        | Mapping[str, Any]
        | tuple[FrontierWorkItem | Mapping[str, Any], ...]
    ),
) -> tuple[FrontierWorkItem | Mapping[str, Any], ...]:
    if isinstance(value, FrontierWorkItem) or isinstance(value, Mapping):
        return (cast(FrontierWorkItem | Mapping[str, Any], value),)
    return tuple(value)


def _frontier_mapping(value: FrontierWorkItem | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(value, FrontierWorkItem):
        return value.to_dict()
    row = dict(value)
    issues = validate_frontier_work_item(row)
    if issues:
        raise ValueError("; ".join(issues))
    return row


def _frontier_report_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **dict(row),
        "surface": "frontier_work_item",
        "row_id": str(row.get("work_item_id") or ""),
        "subject_id": str(row.get("source_artifact_id") or ""),
        "status": str(row.get("frontier_status") or ""),
        "frontier_ref": str(row.get("work_item_id") or ""),
        "forbidden_shortcuts": tuple(
            dict.fromkeys(
                (
                    *tuple(str(item) for item in _sequence(row.get("forbidden_shortcuts"))),
                    *_FRONTIER_WORK_ITEM_REPORT_FORBIDDEN_SHORTCUTS,
                )
            )
        ),
    }


def _report_jurisdiction(rows: tuple[Mapping[str, Any], ...]) -> str:
    jurisdictions = tuple(
        dict.fromkeys(str(row.get("jurisdiction") or "") for row in rows if row.get("jurisdiction"))
    )
    if len(jurisdictions) == 1:
        return jurisdictions[0]
    if len(jurisdictions) > 1:
        return "mixed"
    return ""


def _template_claim_kind(row: Mapping[str, Any]) -> str:
    template = row.get("suggested_claim_template") or {}
    if isinstance(template, Mapping):
        return str(template.get("claim_kind") or "")
    return ""


def _claim_assertion_mapping(assertion: Any) -> Mapping[str, Any]:
    from lawvm.core.provenance_graph import ProvenanceAssertion

    if isinstance(assertion, ProvenanceAssertion):
        return {
            "assertion_id": assertion.assertion_id,
            "jurisdiction": assertion.jurisdiction,
            "kind": assertion.kind,
            "scope": assertion.scope,
            "target": assertion.target,
            "value": assertion.value,
        }
    if isinstance(assertion, Mapping):
        return assertion
    raise TypeError("assertion must be a ProvenanceAssertion or mapping")


def _authorization_result_mapping(result: Any) -> Mapping[str, Any]:
    from lawvm.core.evidence_kernel import AuthorizationResult

    if isinstance(result, AuthorizationResult):
        return {
            "subject_id": result.subject.artifact_id,
            "policy_id": result.policy_id,
            "profile_name": result.profile_name,
            "authorized": result.authorized,
            "satisfied_clauses": result.satisfied_clauses,
            "unsatisfied_clauses": result.unsatisfied_clauses,
            "forbidden_present": result.forbidden_present,
            "evidence_bundle_hash": result.evidence_bundle_hash,
        }
    if isinstance(result, Mapping):
        return result
    raise TypeError("authorization_result must be an AuthorizationResult or mapping")


def _claim_frontier_ref(claim: Mapping[str, Any]) -> str:
    for field_name in ("target", "scope", "value"):
        value = claim.get(field_name)
        if isinstance(value, Mapping):
            frontier_ref = str(value.get("frontier_ref") or "")
            if frontier_ref:
                return frontier_ref
    return ""


def _sequence(value: Any) -> tuple[Any, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, list | tuple):
        return tuple(value)
    return ()


def _registered_claim_kind_spec(claim_kind: str) -> Any:
    if not claim_kind:
        return None
    from lawvm.core.manual_claims.kind_registry import get_claim_kind_spec

    return get_claim_kind_spec(claim_kind)


def _counts(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value or "__blank__")
        counts[key] = counts.get(key, 0) + 1
    return counts


def _plain_jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_jsonable(inner) for key, inner in value.items()}
    if isinstance(value, list | tuple):
        return [_plain_jsonable(inner) for inner in value]
    if isinstance(value, set | frozenset):
        return sorted((_plain_jsonable(inner) for inner in value), key=repr)
    return value
