"""Shared source-chain completeness proof-surface projection.

Source completeness is source-chain diagnostics.  It records whether the
compiler had source artifacts and dates for the amendment chain.  It is not
source identity proof, commencement proof, or replay authorization.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from lawvm.core.evidence_surface_report import EvidenceSurfaceReport
from lawvm.core.execution_authorization import ExecutionAuthorization
from lawvm.core.frozen_values import freeze_mapping
from lawvm.core.quirks_disposition import QuirksDisposition


_SOURCE_COMPLETENESS_REQUIRED_PROOFS: tuple[str, ...] = (
    "source_identity_proof",
    "commencement_or_effective_date_proof",
    "phase_local_replay_authorization",
)
_SOURCE_COMPLETENESS_FORBIDDEN_SHORTCUTS: tuple[str, ...] = (
    "source_completeness_status_as_replay_authorization",
    "source_available_count_as_source_identity_proof",
    "date_available_count_as_commencement_proof",
)
_SOURCE_COMPLETENESS_SAFE_DEFAULT = (
    "treat_source_completeness_as_diagnostic_not_replay_authorization"
)
SOURCE_COMPLETENESS_ORACLE_SUSPECT_FAMILIES = frozenset({
    "oracle_version_effective_after_cutoff",
    "oracle_version_expired_before_cutoff",
    "oracle_missing_version_pin",
})
SOURCE_COMPLETENESS_PENDING_FAMILIES = frozenset({
    "pending_future_effect_after_cutoff",
})


def format_source_completeness_issue_detail(
    families: tuple[str, ...] | list[str],
    reasons: tuple[str, ...] | list[str],
) -> str:
    """Compact report detail for source-completeness issue-family columns."""

    parts: list[str] = []
    for family in families:
        if family:
            parts.append(str(family))
    for reason in reasons:
        if reason:
            parts.append(str(reason))
    return "|".join(parts)


def source_completeness_has_oracle_suspect_family(families: tuple[str, ...] | list[str]) -> bool:
    """Return whether issue families describe oracle-version drift, not missing source XML."""

    return any(family in SOURCE_COMPLETENESS_ORACLE_SUSPECT_FAMILIES for family in families)


def source_completeness_has_pending_family(families: tuple[str, ...] | list[str]) -> bool:
    """Return whether issue families require a later oracle-version check."""

    return any(family in SOURCE_COMPLETENESS_PENDING_FAMILIES for family in families)


@dataclass(frozen=True, slots=True)
class SourceCompletenessStatus:
    """Report-facing source-chain completeness status.

    Counts are factual diagnostics only.  A complete count set means the chain
    had source/date coverage according to the frontend, not that replay is
    authorized or that each source/date has been independently proved.
    """

    jurisdiction: str
    statute_id: str
    chain_length: int
    source_available: int
    dates_available: int
    owner_phase: str = "source_chain_elaboration"
    safe_default: str = _SOURCE_COMPLETENESS_SAFE_DEFAULT
    forbidden_shortcuts: tuple[str, ...] = _SOURCE_COMPLETENESS_FORBIDDEN_SHORTCUTS
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "jurisdiction", _required_string("jurisdiction", self.jurisdiction))
        object.__setattr__(self, "statute_id", str(self.statute_id or ""))
        object.__setattr__(self, "owner_phase", _required_string("owner_phase", self.owner_phase))
        object.__setattr__(self, "chain_length", _nonnegative_int("chain_length", self.chain_length))
        object.__setattr__(self, "source_available", _nonnegative_int("source_available", self.source_available))
        object.__setattr__(self, "dates_available", _nonnegative_int("dates_available", self.dates_available))
        if self.source_available > self.chain_length:
            raise ValueError("SourceCompletenessStatus.source_available cannot exceed chain_length")
        if self.dates_available > self.chain_length:
            raise ValueError("SourceCompletenessStatus.dates_available cannot exceed chain_length")
        object.__setattr__(
            self,
            "forbidden_shortcuts",
            _string_tuple("forbidden_shortcuts", self.forbidden_shortcuts),
        )
        if not self.safe_default:
            raise ValueError("SourceCompletenessStatus.safe_default is required")
        if not isinstance(self.detail, Mapping):
            raise ValueError("SourceCompletenessStatus.detail must be a mapping")
        object.__setattr__(self, "detail", freeze_mapping(self.detail))

    @property
    def missing_sources(self) -> int:
        return max(self.chain_length - self.source_available, 0)

    @property
    def missing_dates(self) -> int:
        return max(self.chain_length - self.dates_available, 0)

    @property
    def status(self) -> str:
        return "complete" if self.missing_sources == 0 and self.missing_dates == 0 else "incomplete"

    @property
    def row_id(self) -> str:
        return f"{self.jurisdiction}:{self.statute_id or 'unknown'}:source-completeness"

    @property
    def counts(self) -> dict[str, int]:
        return {
            "chain_length": self.chain_length,
            "source_available": self.source_available,
            "dates_available": self.dates_available,
            "missing_sources": self.missing_sources,
            "missing_dates": self.missing_dates,
        }

    def to_execution_authorization(self) -> ExecutionAuthorization:
        return ExecutionAuthorization(
            executable=False,
            replay_authorized=False,
            authorization_status=(
                "source_chain_complete_not_replay_authority"
                if self.status == "complete"
                else "source_chain_incomplete"
            ),
            authorization_rule_id=f"{self.row_id}:source-chain-completeness",
            owner_phase=self.owner_phase,
            strict_disposition="record" if self.status == "complete" else "block",
            quirks_disposition=QuirksDisposition.RECORD,
            validator_status="source_chain_completeness_counts_only",
            required_proofs=_SOURCE_COMPLETENESS_REQUIRED_PROOFS,
            safe_default=self.safe_default,
            forbidden_shortcuts=self.forbidden_shortcuts,
            detail={
                **dict(self.detail),
                "jurisdiction": self.jurisdiction,
                "statute_id": self.statute_id,
                "counts": self.counts,
                "row_status": self.status,
            },
        )

    def to_dict(self) -> dict[str, Any]:
        authorization = self.to_execution_authorization().to_dict()
        authorization_ref = str(authorization["authorization_rule_id"])
        return {
            "row_id": self.row_id,
            "subject_id": self.statute_id or self.row_id,
            "jurisdiction": self.jurisdiction,
            "statute_id": self.statute_id,
            "row_status": self.status,
            "owner_phase": self.owner_phase,
            "counts": self.counts,
            "executable": False,
            "replay_authorized": False,
            "authorization_status": authorization["authorization_status"],
            "authorization_ref": authorization_ref,
            "execution_authorization": authorization,
            "safe_default": self.safe_default,
            "forbidden_shortcuts": list(self.forbidden_shortcuts),
            "detail": _plain_jsonable(self.detail),
        }


def source_completeness_status_from_mapping(
    payload: Mapping[str, Any],
    *,
    jurisdiction: str,
    owner_phase: str = "source_chain_elaboration",
) -> SourceCompletenessStatus | None:
    """Build a status object from report payload source-completeness counts."""

    source_completeness = payload.get("source_completeness")
    if not isinstance(source_completeness, Mapping):
        return None
    chain_length = _coerce_nonnegative(source_completeness.get("chain_length"))
    source_available = _coerce_nonnegative(source_completeness.get("source_available"))
    dates_available = _coerce_nonnegative(source_completeness.get("dates_available"))
    if chain_length == 0 and source_available == 0 and dates_available == 0:
        return None
    return SourceCompletenessStatus(
        jurisdiction=jurisdiction,
        statute_id=str(payload.get("statute_id") or ""),
        chain_length=chain_length,
        source_available=source_available,
        dates_available=dates_available,
        owner_phase=owner_phase,
        detail={
            "source_completeness_input": dict(source_completeness),
        },
    )


def source_completeness_evidence_report(
    statuses: SourceCompletenessStatus | tuple[SourceCompletenessStatus, ...],
    *,
    jurisdiction: str,
    report_kind: str = "source_completeness_status",
) -> EvidenceSurfaceReport:
    """Project source-completeness statuses into a shared evidence report."""

    status_rows = statuses if isinstance(statuses, tuple) else (statuses,)
    rows = tuple(
        {"surface": "source_completeness_status", **status.to_dict()}
        for status in status_rows
    )
    status_counts: dict[str, int] = {}
    total_missing_sources = 0
    total_missing_dates = 0
    for status in status_rows:
        status_counts[status.status] = status_counts.get(status.status, 0) + 1
        total_missing_sources += status.missing_sources
        total_missing_dates += status.missing_dates
    summary = {
        "source_completeness_status_count": len(status_rows),
        "status_counts": status_counts,
        "missing_sources": total_missing_sources,
        "missing_dates": total_missing_dates,
    }
    return EvidenceSurfaceReport(
        jurisdiction=jurisdiction,
        report_kind=report_kind,
        schema="lawvm.source_completeness_status.v1",
        truth_claim="source-chain completeness diagnostics",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        summary=summary,
        filters={"report_kind": report_kind},
        filtered_summary=summary,
        rows=rows,
        rows_truncated=False,
        detail={
            "safe_default": _SOURCE_COMPLETENESS_SAFE_DEFAULT,
            "forbidden_shortcuts": _SOURCE_COMPLETENESS_FORBIDDEN_SHORTCUTS,
            "included_surfaces": ("source_completeness_status",),
        },
    )


def _required_string(field_name: str, value: Any) -> str:
    text = str(value or "")
    if not text:
        raise ValueError(f"SourceCompletenessStatus.{field_name} is required")
    return text


def _nonnegative_int(field_name: str, value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"SourceCompletenessStatus.{field_name} must be a non-negative int")
    return value


def _coerce_nonnegative(value: Any) -> int:
    try:
        numeric = int(value or 0)
    except (TypeError, ValueError):
        numeric = 0
    return max(numeric, 0)


def _string_tuple(field_name: str, values: Any) -> tuple[str, ...]:
    if isinstance(values, str) or not isinstance(values, tuple):
        raise ValueError(f"SourceCompletenessStatus.{field_name} must be a tuple")
    result = tuple(str(value) for value in values if str(value))
    if not result:
        raise ValueError(f"SourceCompletenessStatus.{field_name} must not be empty")
    return result


def _plain_jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_jsonable(inner) for key, inner in value.items()}
    if isinstance(value, list | tuple):
        return [_plain_jsonable(inner) for inner in value]
    if isinstance(value, set | frozenset):
        return sorted((_plain_jsonable(inner) for inner in value), key=repr)
    return value
