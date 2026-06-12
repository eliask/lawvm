"""Shared proof-gate summary contract.

The summary is accounting over already-visible proof surfaces. It does not
authorize replay and it does not prove that a frontier has been closed.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Mapping

from lawvm.core.frozen_values import freeze_mapping
from lawvm.core.temporal_resolution import (
    TEMPORAL_FUTURE_EFFECTIVE_DATE,
    TEMPORAL_UNKNOWN_EFFECTIVE_DATE,
    TEMPORAL_UNRESOLVED_CONTINGENT,
)


_UNRESOLVED_TEMPORAL_STATUSES = frozenset(
    {
        TEMPORAL_FUTURE_EFFECTIVE_DATE,
        TEMPORAL_UNKNOWN_EFFECTIVE_DATE,
        TEMPORAL_UNRESOLVED_CONTINGENT,
    }
)
_BLOCKED_RECOVERY_AUTHORIZATION_STATUSES = frozenset(
    {
        "strict_recovery_blocked",
    }
)


@dataclass(frozen=True, slots=True)
class ProofGateSummary:
    """Aggregate open proof/frontier signals without promoting them."""

    schema: str
    scope: str
    closed: bool
    open_gate_signal_count: int
    ownership_failed_gate_count: int
    ownership_failed_gate_counts: Mapping[str, int] = field(default_factory=dict)
    unowned_counts: Mapping[str, int] = field(default_factory=dict)
    frontier_work_item_count: int = 0
    manual_claim_frontier_count: int = 0
    coverage_frontier_count: int = 0
    other_frontier_count: int = 0
    frontier_owner_phase_counts: Mapping[str, int] = field(default_factory=dict)
    frontier_status_counts: Mapping[str, int] = field(default_factory=dict)
    required_claim_kind_counts: Mapping[str, int] = field(default_factory=dict)
    manual_frontier_required_claim_kind_counts: Mapping[str, int] = field(default_factory=dict)
    manual_frontier_status_counts: Mapping[str, int] = field(default_factory=dict)
    coverage_frontier_required_claim_kind_counts: Mapping[str, int] = field(default_factory=dict)
    coverage_frontier_status_counts: Mapping[str, int] = field(default_factory=dict)
    other_frontier_required_claim_kind_counts: Mapping[str, int] = field(default_factory=dict)
    other_frontier_status_counts: Mapping[str, int] = field(default_factory=dict)
    incomplete_candidate_set_count: int = 0
    candidate_set_completeness_counts: Mapping[str, int] = field(default_factory=dict)
    source_completeness_counts: Mapping[str, int] = field(default_factory=dict)
    source_completeness_missing_count: int = 0
    source_unit_coverage_status_counts: Mapping[str, int] = field(default_factory=dict)
    source_unit_unresolved_count: int = 0
    potential_operation_classification_counts: Mapping[str, int] = field(default_factory=dict)
    potential_operation_unresolved_count: int = 0
    regex_recognition_coverage_status_counts: Mapping[str, int] = field(default_factory=dict)
    regex_recognition_unclassified_gap_count: int = 0
    temporal_resolution_status_counts: Mapping[str, int] = field(default_factory=dict)
    temporal_resolution_unresolved_count: int = 0
    recovery_authorization_status_counts: Mapping[str, int] = field(default_factory=dict)
    recovery_authorization_blocked_count: int = 0
    safe_default: str = "treat_open_proof_gates_as_non_executable_frontier_accounting"
    does_not_claim: tuple[str, ...] = (
        "proof_closure",
        "source_unit_enumeration_closure",
        "source_chain_completeness",
        "operation_cue_exhaustiveness",
        "source_unit_unresolved_closure",
        "potential_operation_unresolved_closure",
        "regex_recognition_gap_closure",
        "temporal_resolution_closure",
        "recovery_authorization_closure",
        "replay_authorization",
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema", _required_string("schema", self.schema))
        object.__setattr__(self, "scope", _required_string("scope", self.scope))
        if not isinstance(self.closed, bool):
            raise ValueError("ProofGateSummary.closed must be boolean")
        for field_name in (
            "open_gate_signal_count",
            "ownership_failed_gate_count",
            "frontier_work_item_count",
            "manual_claim_frontier_count",
            "coverage_frontier_count",
            "other_frontier_count",
            "incomplete_candidate_set_count",
            "source_completeness_missing_count",
            "source_unit_unresolved_count",
            "potential_operation_unresolved_count",
            "regex_recognition_unclassified_gap_count",
            "temporal_resolution_unresolved_count",
            "recovery_authorization_blocked_count",
        ):
            _require_nonnegative_int(field_name, getattr(self, field_name))
        for field_name in (
            "ownership_failed_gate_counts",
            "unowned_counts",
            "frontier_owner_phase_counts",
            "frontier_status_counts",
            "required_claim_kind_counts",
            "manual_frontier_required_claim_kind_counts",
            "manual_frontier_status_counts",
            "coverage_frontier_required_claim_kind_counts",
            "coverage_frontier_status_counts",
            "other_frontier_required_claim_kind_counts",
            "other_frontier_status_counts",
            "candidate_set_completeness_counts",
            "source_completeness_counts",
            "source_unit_coverage_status_counts",
            "potential_operation_classification_counts",
            "regex_recognition_coverage_status_counts",
            "temporal_resolution_status_counts",
            "recovery_authorization_status_counts",
        ):
            object.__setattr__(self, field_name, freeze_mapping(_count_mapping(getattr(self, field_name))))
        object.__setattr__(self, "safe_default", _required_string("safe_default", self.safe_default))
        object.__setattr__(
            self,
            "does_not_claim",
            tuple(str(item) for item in self.does_not_claim if str(item)),
        )
        if "replay_authorization" not in self.does_not_claim:
            raise ValueError("ProofGateSummary.does_not_claim must include replay_authorization")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "scope": self.scope,
            "closed": self.closed,
            "open_gate_signal_count": self.open_gate_signal_count,
            "ownership_failed_gate_count": self.ownership_failed_gate_count,
            "ownership_failed_gate_counts": dict(self.ownership_failed_gate_counts),
            "unowned_counts": dict(self.unowned_counts),
            "frontier_work_item_count": self.frontier_work_item_count,
            "manual_claim_frontier_count": self.manual_claim_frontier_count,
            "coverage_frontier_count": self.coverage_frontier_count,
            "other_frontier_count": self.other_frontier_count,
            "frontier_owner_phase_counts": dict(self.frontier_owner_phase_counts),
            "frontier_status_counts": dict(self.frontier_status_counts),
            "required_claim_kind_counts": dict(self.required_claim_kind_counts),
            "manual_frontier_required_claim_kind_counts": dict(
                self.manual_frontier_required_claim_kind_counts
            ),
            "manual_frontier_status_counts": dict(self.manual_frontier_status_counts),
            "coverage_frontier_required_claim_kind_counts": dict(
                self.coverage_frontier_required_claim_kind_counts
            ),
            "coverage_frontier_status_counts": dict(self.coverage_frontier_status_counts),
            "other_frontier_required_claim_kind_counts": dict(
                self.other_frontier_required_claim_kind_counts
            ),
            "other_frontier_status_counts": dict(self.other_frontier_status_counts),
            "incomplete_candidate_set_count": self.incomplete_candidate_set_count,
            "candidate_set_completeness_counts": dict(self.candidate_set_completeness_counts),
            "source_completeness_counts": dict(self.source_completeness_counts),
            "source_completeness_missing_count": self.source_completeness_missing_count,
            "source_unit_coverage_status_counts": dict(self.source_unit_coverage_status_counts),
            "source_unit_unresolved_count": self.source_unit_unresolved_count,
            "potential_operation_classification_counts": dict(
                self.potential_operation_classification_counts
            ),
            "potential_operation_unresolved_count": (
                self.potential_operation_unresolved_count
            ),
            "regex_recognition_coverage_status_counts": dict(
                self.regex_recognition_coverage_status_counts
            ),
            "regex_recognition_unclassified_gap_count": (
                self.regex_recognition_unclassified_gap_count
            ),
            "temporal_resolution_status_counts": dict(
                self.temporal_resolution_status_counts
            ),
            "temporal_resolution_unresolved_count": (
                self.temporal_resolution_unresolved_count
            ),
            "recovery_authorization_status_counts": dict(
                self.recovery_authorization_status_counts
            ),
            "recovery_authorization_blocked_count": (
                self.recovery_authorization_blocked_count
            ),
            "safe_default": self.safe_default,
            "does_not_claim": list(self.does_not_claim),
        }


def proof_gate_summary_from_surfaces(
    *,
    schema: str,
    scope: str,
    closed: bool,
    failed_gates: Any,
    unowned_counts: Mapping[str, Any] | None = None,
    manual_or_other_frontier_work_items: Any = (),
    coverage_frontier_work_items: Any = (),
    candidate_set_certificates: Any = (),
    evidence_summary: Mapping[str, Any] | None = None,
    manual_claim_kind_prefixes: tuple[str, ...] = (),
    safe_default: str = "treat_open_proof_gates_as_non_executable_frontier_accounting",
    does_not_claim: tuple[str, ...] = (
        "proof_closure",
        "source_unit_enumeration_closure",
        "source_chain_completeness",
        "operation_cue_exhaustiveness",
        "source_unit_unresolved_closure",
        "potential_operation_unresolved_closure",
        "regex_recognition_gap_closure",
        "temporal_resolution_closure",
        "recovery_authorization_closure",
        "replay_authorization",
    ),
) -> ProofGateSummary:
    """Build passive proof-gate accounting from shared report rows."""

    failed_gate_rows = tuple(str(gate) for gate in _sequence(failed_gates) if str(gate))
    unowned = _nonzero_count_mapping(unowned_counts or {})
    coverage_frontiers = _mapping_rows(coverage_frontier_work_items)
    non_coverage_frontiers = _mapping_rows(manual_or_other_frontier_work_items)
    manual_claim_frontiers = tuple(
        row
        for row in non_coverage_frontiers
        if _matches_prefix(row.get("required_claim_kind"), manual_claim_kind_prefixes)
    )
    manual_ids = {id(row) for row in manual_claim_frontiers}
    other_frontiers = tuple(
        row for row in non_coverage_frontiers if id(row) not in manual_ids
    )
    all_frontiers = (*manual_claim_frontiers, *other_frontiers, *coverage_frontiers)
    candidate_sets = _mapping_rows(candidate_set_certificates)
    incomplete_candidate_sets = tuple(
        row
        for row in candidate_sets
        if str(row.get("completeness_status") or "") != "complete"
    )
    evidence = dict(evidence_summary or {})
    source_completeness_counts = _summary_count_mapping(
        evidence,
        "source_completeness",
    )
    source_completeness_missing_count = (
        source_completeness_counts.get("missing_sources", 0)
        + source_completeness_counts.get("missing_dates", 0)
    )
    source_unit_counts = _summary_count_mapping(
        evidence,
        "source_unit_coverage_status_counts",
    )
    source_unit_unresolved_count = (
        source_unit_counts.get("unclassified", 0) + source_unit_counts.get("blocked", 0)
    )
    potential_operation_counts = _summary_count_mapping(
        evidence,
        "potential_operation_classification_counts",
    )
    potential_operation_unresolved_count = (
        potential_operation_counts.get("unclassified", 0)
        + potential_operation_counts.get("blocked", 0)
    )
    regex_unclassified_gap_count = _summary_count(
        evidence,
        "regex_recognition_unclassified_gap_count",
    )
    temporal_resolution_counts = _summary_count_mapping(
        evidence,
        "temporal_resolution_status_counts",
    )
    temporal_resolution_unresolved_count = sum(
        temporal_resolution_counts.get(status, 0)
        for status in _UNRESOLVED_TEMPORAL_STATUSES
    )
    recovery_authorization_counts = _summary_count_mapping(
        evidence,
        "recovery_execution_authorization_status_counts",
    )
    recovery_authorization_blocked_count = sum(
        recovery_authorization_counts.get(status, 0)
        for status in _BLOCKED_RECOVERY_AUTHORIZATION_STATUSES
    )
    open_gate_signal_count = (
        len(failed_gate_rows)
        + sum(unowned.values())
        + len(all_frontiers)
        + len(incomplete_candidate_sets)
        + source_completeness_missing_count
        + source_unit_unresolved_count
        + potential_operation_unresolved_count
        + regex_unclassified_gap_count
        + temporal_resolution_unresolved_count
        + recovery_authorization_blocked_count
    )
    return ProofGateSummary(
        schema=schema,
        scope=scope,
        closed=closed,
        open_gate_signal_count=open_gate_signal_count,
        ownership_failed_gate_count=len(failed_gate_rows),
        ownership_failed_gate_counts=_count_values(failed_gate_rows),
        unowned_counts=unowned,
        frontier_work_item_count=len(all_frontiers),
        manual_claim_frontier_count=len(manual_claim_frontiers),
        coverage_frontier_count=len(coverage_frontiers),
        other_frontier_count=len(other_frontiers),
        frontier_owner_phase_counts=_count_values(row.get("owner_phase") for row in all_frontiers),
        frontier_status_counts=_count_values(row.get("frontier_status") for row in all_frontiers),
        required_claim_kind_counts=_count_values(row.get("required_claim_kind") for row in all_frontiers),
        manual_frontier_required_claim_kind_counts=_count_values(
            row.get("required_claim_kind") for row in manual_claim_frontiers
        ),
        manual_frontier_status_counts=_count_values(
            row.get("frontier_status") for row in manual_claim_frontiers
        ),
        coverage_frontier_required_claim_kind_counts=_count_values(
            row.get("required_claim_kind") for row in coverage_frontiers
        ),
        coverage_frontier_status_counts=_count_values(
            row.get("frontier_status") for row in coverage_frontiers
        ),
        other_frontier_required_claim_kind_counts=_count_values(
            row.get("required_claim_kind") for row in other_frontiers
        ),
        other_frontier_status_counts=_count_values(
            row.get("frontier_status") for row in other_frontiers
        ),
        incomplete_candidate_set_count=len(incomplete_candidate_sets),
        candidate_set_completeness_counts=_count_values(
            row.get("completeness_status") for row in candidate_sets
        ),
        source_completeness_counts=source_completeness_counts,
        source_completeness_missing_count=source_completeness_missing_count,
        source_unit_coverage_status_counts=source_unit_counts,
        source_unit_unresolved_count=source_unit_unresolved_count,
        potential_operation_classification_counts=potential_operation_counts,
        potential_operation_unresolved_count=potential_operation_unresolved_count,
        regex_recognition_coverage_status_counts=dict(
            evidence.get("regex_recognition_coverage_status_counts") or {}
        ),
        regex_recognition_unclassified_gap_count=regex_unclassified_gap_count,
        temporal_resolution_status_counts=temporal_resolution_counts,
        temporal_resolution_unresolved_count=temporal_resolution_unresolved_count,
        recovery_authorization_status_counts=recovery_authorization_counts,
        recovery_authorization_blocked_count=recovery_authorization_blocked_count,
        safe_default=safe_default,
        does_not_claim=does_not_claim,
    )


def _required_string(field_name: str, value: Any) -> str:
    text = str(value or "")
    if not text:
        raise ValueError(f"ProofGateSummary.{field_name} is required")
    return text


def _require_nonnegative_int(field_name: str, value: Any) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"ProofGateSummary.{field_name} must be a non-negative integer")


def _summary_count(evidence: Mapping[str, Any], field_name: str) -> int:
    value = evidence.get(field_name, 0)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"ProofGateSummary.{field_name} must be a non-negative integer")
    return value


def _summary_count_mapping(evidence: Mapping[str, Any], field_name: str) -> dict[str, int]:
    value = evidence.get(field_name, {})
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"ProofGateSummary.{field_name} must be a mapping")
    try:
        return _count_mapping(value)
    except ValueError as exc:
        raise ValueError(f"ProofGateSummary.{field_name}: {exc}") from exc


def _count_mapping(value: Mapping[str, Any]) -> dict[str, int]:
    if not isinstance(value, Mapping):
        raise ValueError("ProofGateSummary count fields must be mappings")
    return _nonzero_count_mapping(value)


def _nonzero_count_mapping(value: Mapping[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for key, count in value.items():
        if not str(key):
            continue
        if not isinstance(count, int) or isinstance(count, bool):
            raise ValueError("ProofGateSummary count values must be non-negative integers")
        if count < 0:
            raise ValueError("ProofGateSummary count values must be non-negative integers")
        if count:
            counts[str(key)] = count
    return counts


def _count_values(values: Any) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for value in values:
        text = str(value or "")
        if text:
            counts[text] += 1
    return dict(sorted(counts.items()))


def _mapping_rows(value: Any) -> tuple[dict[str, Any], ...]:
    if isinstance(value, Mapping):
        return (dict(value),)
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(dict(row) for row in value if isinstance(row, Mapping))


def _sequence(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        return (value,)
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(value)
    return (value,)


def _matches_prefix(value: Any, prefixes: tuple[str, ...]) -> bool:
    text = str(value or "")
    return bool(text) and any(text.startswith(prefix) for prefix in prefixes)
