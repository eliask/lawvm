"""Shared ownership-closure certificate for bounded corpus slices."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from lawvm.core.evidence_surface_report import EvidenceSurfaceReport
from lawvm.core.frozen_values import freeze_mapping

OWNERSHIP_CLOSURE_SCHEMA = "lawvm.ownership_closure_coverage.v1"
_OWNERSHIP_CLOSURE_REPORT_FORBIDDEN_SHORTCUTS: tuple[str, ...] = (
    "ownership_closure_coverage_as_replay_authorization",
    "ownership_closure_coverage_as_full_corpus_omniscience",
    "open_ownership_closure_as_compile_failure",
)


@dataclass(frozen=True, slots=True)
class OwnershipClosureCoverage:
    """Accounting certificate for a declared source/candidate/result slice.

    The certificate is passive: it does not authorize replay and does not claim
    legal omniscience.  It only states whether the declared slice has no visible
    unowned source units, operation candidates, lifecycle rows, or residuals.
    """

    certificate_id: str
    corpus_slice_id: str
    source_bundle_hash: str
    profile_id: str
    interpretation_policy_id: str
    graph_snapshot_hash: str
    phase_report_ids: Mapping[str, str]
    closed: bool
    failed_gates: tuple[str, ...] = ()
    unowned_counts: Mapping[str, int] = field(default_factory=dict)
    owned_counts: Mapping[str, int] = field(default_factory=dict)
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name, value in (
            ("certificate_id", self.certificate_id),
            ("corpus_slice_id", self.corpus_slice_id),
            ("source_bundle_hash", self.source_bundle_hash),
            ("profile_id", self.profile_id),
            ("interpretation_policy_id", self.interpretation_policy_id),
            ("graph_snapshot_hash", self.graph_snapshot_hash),
        ):
            object.__setattr__(
                self,
                field_name,
                _required_string(field_name, value),
            )
        if not isinstance(self.closed, bool):
            raise ValueError("OwnershipClosureCoverage.closed must be boolean")
        failed_gates = _string_tuple("failed_gates", self.failed_gates)
        phase_report_ids = _string_mapping("phase_report_ids", self.phase_report_ids)
        unowned_counts = _int_mapping("unowned_counts", self.unowned_counts)
        owned_counts = _int_mapping("owned_counts", self.owned_counts)
        if not isinstance(self.detail, Mapping):
            raise ValueError("OwnershipClosureCoverage.detail must be a mapping")
        if self.closed and failed_gates:
            raise ValueError("OwnershipClosureCoverage.closed requires no failed_gates")
        if self.closed and any(count != 0 for count in unowned_counts.values()):
            raise ValueError("OwnershipClosureCoverage.closed requires all unowned_counts to be zero")
        if self.closed and not phase_report_ids:
            raise ValueError("OwnershipClosureCoverage.closed requires phase_report_ids")
        if self.closed and not _string_sequence(self.detail.get("closure_dimensions")):
            raise ValueError("OwnershipClosureCoverage.closed requires detail.closure_dimensions")
        object.__setattr__(self, "failed_gates", failed_gates)
        object.__setattr__(self, "phase_report_ids", phase_report_ids)
        object.__setattr__(self, "unowned_counts", unowned_counts)
        object.__setattr__(self, "owned_counts", owned_counts)
        object.__setattr__(self, "detail", freeze_mapping(self.detail))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": OWNERSHIP_CLOSURE_SCHEMA,
            "certificate_id": self.certificate_id,
            "corpus_slice_id": self.corpus_slice_id,
            "source_bundle_hash": self.source_bundle_hash,
            "profile_id": self.profile_id,
            "interpretation_policy_id": self.interpretation_policy_id,
            "graph_snapshot_hash": self.graph_snapshot_hash,
            "phase_report_ids": dict(self.phase_report_ids),
            "closure_status": "closed" if self.closed else "open",
            "closed": self.closed,
            "failed_gates": list(self.failed_gates),
            "unowned_counts": dict(self.unowned_counts),
            "owned_counts": dict(self.owned_counts),
            "detail": _plain_jsonable(self.detail),
        }


def ownership_closure_evidence_report(
    certificates: OwnershipClosureCoverage | tuple[OwnershipClosureCoverage, ...],
    *,
    jurisdiction: str,
    report_kind: str = "ownership_closure",
) -> EvidenceSurfaceReport:
    """Project closure certificates into the shared passive report envelope."""

    rows = certificates if isinstance(certificates, tuple) else (certificates,)
    closed_count = sum(1 for row in rows if row.closed)
    open_count = len(rows) - closed_count
    unowned_counts = _sum_count_maps(row.unowned_counts for row in rows)
    owned_counts = _sum_count_maps(row.owned_counts for row in rows)
    failed_gate_counts = _counts(gate for row in rows for gate in row.failed_gates)
    summary = {
        "certificate_count": len(rows),
        "closed_count": closed_count,
        "open_count": open_count,
        "unowned_counts": unowned_counts,
        "owned_counts": owned_counts,
        "failed_gate_counts": failed_gate_counts,
        "claim_flags": {
            "replay_claims": False,
            "canonical_effect_claims": False,
            "candidate_effect_claims": False,
            "dry_run_claims": False,
            "agreement_claims": False,
        },
    }
    return EvidenceSurfaceReport(
        jurisdiction=jurisdiction,
        report_kind=report_kind,
        schema="lawvm.ownership_closure_report.v1",
        truth_claim="bounded ownership accounting closure",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        summary=summary,
        rows=tuple(_ownership_closure_report_row(row) for row in rows),
        detail={
            "safe_default": "treat_open_closure_as_declared_accounting_gap_not_replay_authority",
            "forbidden_shortcuts": _OWNERSHIP_CLOSURE_REPORT_FORBIDDEN_SHORTCUTS,
            "included_surfaces": ("ownership_closure_coverage",),
        },
    )


def _ownership_closure_report_row(certificate: OwnershipClosureCoverage) -> dict[str, Any]:
    row = certificate.to_dict()
    return {
        **row,
        "surface": "ownership_closure_coverage",
        "row_id": certificate.certificate_id,
        "subject_id": certificate.corpus_slice_id,
        "row_status": row["closure_status"],
        "closure_ref": certificate.certificate_id,
        "forbidden_shortcuts": _OWNERSHIP_CLOSURE_REPORT_FORBIDDEN_SHORTCUTS,
    }


def _required_string(field_name: str, value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"OwnershipClosureCoverage.{field_name} is required")
    return text


def _string_tuple(field_name: str, values: Any) -> tuple[str, ...]:
    if isinstance(values, str) or not isinstance(values, tuple):
        raise ValueError(f"OwnershipClosureCoverage.{field_name} must be a tuple")
    return tuple(str(value) for value in values if str(value))


def _string_mapping(field_name: str, value: Any) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError(f"OwnershipClosureCoverage.{field_name} must be a mapping")
    normalized = {str(key): str(item) for key, item in value.items() if str(key) and str(item)}
    return freeze_mapping(normalized)


def _string_sequence(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, list | tuple):
        return tuple(str(item) for item in value if str(item))
    return ()


def _int_mapping(field_name: str, value: Any) -> Mapping[str, int]:
    if not isinstance(value, Mapping):
        raise ValueError(f"OwnershipClosureCoverage.{field_name} must be a mapping")
    normalized: dict[str, int] = {}
    for key, count in value.items():
        if not isinstance(count, int) or isinstance(count, bool):
            raise ValueError(f"OwnershipClosureCoverage.{field_name} values must be integers")
        if count < 0:
            raise ValueError(f"OwnershipClosureCoverage.{field_name} values must be non-negative")
        normalized[str(key)] = count
    return freeze_mapping(normalized)


def _sum_count_maps(values: Any) -> dict[str, int]:
    totals: dict[str, int] = {}
    for mapping in values:
        for key, count in mapping.items():
            totals[str(key)] = totals.get(str(key), 0) + int(count)
    return dict(sorted(totals.items()))


def _counts(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value)
        if key:
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _plain_jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_jsonable(inner) for key, inner in value.items()}
    if isinstance(value, list | tuple):
        return [_plain_jsonable(inner) for inner in value]
    if isinstance(value, set | frozenset):
        return sorted((_plain_jsonable(inner) for inner in value), key=repr)
    return value
