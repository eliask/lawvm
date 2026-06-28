"""Report-facing frontend phase-surface contracts.

These objects describe what a frontend phase produced and what authority that
artifact has. They do not authorize replay and do not replace ``PhaseResult``;
they are the typed proof/report surface for compiler waists such as token
tapes, surface clauses, ClauseAST, compatibility operation projections, and
residual collection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from lawvm.core.evidence_surface_report import EvidenceSurfaceReport
from lawvm.core.frozen_values import freeze_mapping
from lawvm.core.phase_result import Finding
from lawvm.core.quirks_disposition import QuirksDisposition


_FRONTEND_PHASE_FORBIDDEN_SHORTCUTS: tuple[str, ...] = (
    "frontend_phase_surface_as_replay_authorization",
    "compatibility_output_as_semantic_authority",
    "diagnostic_row_as_mutation_instruction",
    "phase_success_as_canonical_effect_proof",
)


@dataclass(frozen=True, slots=True)
class FrontendDiagnostic:
    """Typed diagnostic emitted by a frontend phase surface."""

    diagnostic_id: str
    jurisdiction: str
    frontend: str
    phase: str
    severity: str
    rule_id: str
    message: str
    blocking: bool = False
    strict_disposition: str = "record"
    quirks_disposition: QuirksDisposition = QuirksDisposition.RECORD
    safe_default: str = "record_without_replay_authority"
    forbidden_shortcuts: tuple[str, ...] = ()
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "diagnostic_id",
            _required_string("FrontendDiagnostic.diagnostic_id", self.diagnostic_id),
        )
        object.__setattr__(
            self,
            "jurisdiction",
            _required_string("FrontendDiagnostic.jurisdiction", self.jurisdiction),
        )
        object.__setattr__(
            self,
            "frontend",
            _required_string("FrontendDiagnostic.frontend", self.frontend),
        )
        object.__setattr__(self, "phase", _required_string("FrontendDiagnostic.phase", self.phase))
        object.__setattr__(
            self,
            "severity",
            _required_string("FrontendDiagnostic.severity", self.severity),
        )
        object.__setattr__(self, "rule_id", _required_string("FrontendDiagnostic.rule_id", self.rule_id))
        object.__setattr__(self, "message", _required_string("FrontendDiagnostic.message", self.message))
        object.__setattr__(
            self,
            "strict_disposition",
            _required_string("FrontendDiagnostic.strict_disposition", self.strict_disposition),
        )
        object.__setattr__(
            self,
            "quirks_disposition",
            _required_string("FrontendDiagnostic.quirks_disposition", self.quirks_disposition),
        )
        object.__setattr__(
            self,
            "safe_default",
            _required_string("FrontendDiagnostic.safe_default", self.safe_default),
        )
        if not isinstance(self.blocking, bool):
            raise ValueError("FrontendDiagnostic.blocking must be boolean")
        object.__setattr__(
            self,
            "forbidden_shortcuts",
            _string_tuple("FrontendDiagnostic.forbidden_shortcuts", self.forbidden_shortcuts),
        )
        if not isinstance(self.detail, Mapping):
            raise ValueError("FrontendDiagnostic.detail must be a mapping")
        object.__setattr__(self, "detail", freeze_mapping(self.detail))

    def to_dict(self) -> dict[str, Any]:
        return {
            "diagnostic_id": self.diagnostic_id,
            "jurisdiction": self.jurisdiction,
            "frontend": self.frontend,
            "phase": self.phase,
            "severity": self.severity,
            "rule_id": self.rule_id,
            "message": self.message,
            "blocking": self.blocking,
            "strict_disposition": self.strict_disposition,
            "quirks_disposition": self.quirks_disposition,
            "safe_default": self.safe_default,
            "forbidden_shortcuts": list(self.forbidden_shortcuts),
            "detail": _plain_jsonable(self.detail),
        }


@dataclass(frozen=True, slots=True)
class FrontendPhaseRow:
    """One frontend compiler phase and its report-facing artifact role."""

    phase: str
    phase_status: str
    artifact_kind: str
    authority_role: str
    produced: bool
    input_artifacts: tuple[str, ...] = ()
    output_artifacts: tuple[str, ...] = ()
    diagnostic_ids: tuple[str, ...] = ()
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "phase", _required_string("FrontendPhaseRow.phase", self.phase))
        object.__setattr__(self, "phase_status", _required_string("FrontendPhaseRow.phase_status", self.phase_status))
        object.__setattr__(
            self,
            "artifact_kind",
            _required_string("FrontendPhaseRow.artifact_kind", self.artifact_kind),
        )
        object.__setattr__(
            self,
            "authority_role",
            _required_string("FrontendPhaseRow.authority_role", self.authority_role),
        )
        if not isinstance(self.produced, bool):
            raise ValueError("FrontendPhaseRow.produced must be boolean")
        object.__setattr__(
            self,
            "input_artifacts",
            _string_tuple("FrontendPhaseRow.input_artifacts", self.input_artifacts),
        )
        object.__setattr__(
            self,
            "output_artifacts",
            _string_tuple("FrontendPhaseRow.output_artifacts", self.output_artifacts),
        )
        object.__setattr__(
            self,
            "diagnostic_ids",
            _string_tuple("FrontendPhaseRow.diagnostic_ids", self.diagnostic_ids),
        )
        if not isinstance(self.detail, Mapping):
            raise ValueError("FrontendPhaseRow.detail must be a mapping")
        object.__setattr__(self, "detail", freeze_mapping(self.detail))

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "phase_status": self.phase_status,
            "artifact_kind": self.artifact_kind,
            "authority_role": self.authority_role,
            "produced": self.produced,
            "input_artifacts": list(self.input_artifacts),
            "output_artifacts": list(self.output_artifacts),
            "diagnostic_ids": list(self.diagnostic_ids),
            "detail": _plain_jsonable(self.detail),
        }


@dataclass(frozen=True, slots=True)
class FrontendPhaseSurface:
    """Typed phase map for one frontend parse/lower pass.

    The boolean claim fields make the no-hidden-promotion boundary explicit:
    this surface can describe compatibility ops and diagnostics, but it does
    not by itself claim canonical effects, dry-run authority, replay authority,
    or agreement with an external oracle.
    """

    jurisdiction: str
    frontend: str
    schema: str
    truth_claim: str
    source_hash: str
    source_length: int
    authority_path: tuple[str, ...]
    compatibility_outputs: tuple[str, ...] = ()
    phase_rows: tuple[FrontendPhaseRow, ...] = ()
    diagnostics: tuple[FrontendDiagnostic, ...] = ()
    replay_claims: bool = False
    canonical_effect_claims: bool = False
    dry_run_claims: bool = False
    agreement_claims: bool = False
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "jurisdiction",
            _required_string("FrontendPhaseSurface.jurisdiction", self.jurisdiction),
        )
        object.__setattr__(
            self,
            "frontend",
            _required_string("FrontendPhaseSurface.frontend", self.frontend),
        )
        object.__setattr__(self, "schema", _required_string("FrontendPhaseSurface.schema", self.schema))
        object.__setattr__(
            self,
            "truth_claim",
            _required_string("FrontendPhaseSurface.truth_claim", self.truth_claim),
        )
        object.__setattr__(
            self,
            "source_hash",
            _required_string("FrontendPhaseSurface.source_hash", self.source_hash),
        )
        if not isinstance(self.source_length, int) or self.source_length < 0:
            raise ValueError("FrontendPhaseSurface.source_length must be a non-negative integer")
        object.__setattr__(
            self,
            "authority_path",
            _string_tuple("FrontendPhaseSurface.authority_path", self.authority_path),
        )
        if not self.authority_path:
            raise ValueError("FrontendPhaseSurface.authority_path is required")
        object.__setattr__(
            self,
            "compatibility_outputs",
            _string_tuple("FrontendPhaseSurface.compatibility_outputs", self.compatibility_outputs),
        )
        rows = tuple(self.phase_rows)
        if not all(isinstance(row, FrontendPhaseRow) for row in rows):
            raise ValueError("FrontendPhaseSurface.phase_rows must contain FrontendPhaseRow objects")
        diagnostics = tuple(self.diagnostics)
        if not all(isinstance(row, FrontendDiagnostic) for row in diagnostics):
            raise ValueError("FrontendPhaseSurface.diagnostics must contain FrontendDiagnostic objects")
        object.__setattr__(self, "phase_rows", rows)
        object.__setattr__(self, "diagnostics", diagnostics)
        for field_name, value in (
            ("replay_claims", self.replay_claims),
            ("canonical_effect_claims", self.canonical_effect_claims),
            ("dry_run_claims", self.dry_run_claims),
            ("agreement_claims", self.agreement_claims),
        ):
            if not isinstance(value, bool):
                raise ValueError(f"FrontendPhaseSurface.{field_name} must be boolean")
        if not isinstance(self.detail, Mapping):
            raise ValueError("FrontendPhaseSurface.detail must be a mapping")
        object.__setattr__(self, "detail", freeze_mapping(self.detail))

    def to_dict(self) -> dict[str, Any]:
        return {
            "jurisdiction": self.jurisdiction,
            "frontend": self.frontend,
            "schema": self.schema,
            "truth_claim": self.truth_claim,
            "source_hash": self.source_hash,
            "source_length": self.source_length,
            "authority_path": list(self.authority_path),
            "compatibility_outputs": list(self.compatibility_outputs),
            "phase_rows": [row.to_dict() for row in self.phase_rows],
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
            "replay_claims": self.replay_claims,
            "canonical_effect_claims": self.canonical_effect_claims,
            "dry_run_claims": self.dry_run_claims,
            "agreement_claims": self.agreement_claims,
            "detail": _plain_jsonable(self.detail),
        }


def frontend_phase_surface_evidence_report(
    surface: FrontendPhaseSurface | Mapping[str, Any],
    *,
    report_kind: str = "frontend_phase_surface",
) -> EvidenceSurfaceReport:
    """Project a frontend phase surface into the shared report envelope.

    The projection is read-model evidence only. It lets tooling consume
    frontend phase rows through the same ``EvidenceSurfaceReport`` /
    ``ProofSurface`` path as other proof surfaces, without promoting parser
    outputs, compatibility artifacts, or diagnostics into replay authority.
    """

    data = surface.to_dict() if isinstance(surface, FrontendPhaseSurface) else dict(surface)
    phase_rows = _mapping_rows(data.get("phase_rows"))
    diagnostics = _mapping_rows(data.get("diagnostics"))
    rows = (
        *(_phase_row_report_row(row, data=data) for row in phase_rows),
        *(_diagnostic_report_row(row, data=data) for row in diagnostics),
    )
    summary = {
        "phase_row_count": len(phase_rows),
        "diagnostic_count": len(diagnostics),
        "blocking_diagnostic_count": sum(1 for row in diagnostics if bool(row.get("blocking"))),
        "authority_path": tuple(str(item) for item in _sequence(data.get("authority_path"))),
        "compatibility_outputs": tuple(str(item) for item in _sequence(data.get("compatibility_outputs"))),
        "source_length": _nonnegative_int(data.get("source_length")),
        "claim_flags": {
            "replay_claims": bool(data.get("replay_claims", False)),
            "canonical_effect_claims": bool(data.get("canonical_effect_claims", False)),
            "candidate_effect_claims": False,
            "dry_run_claims": bool(data.get("dry_run_claims", False)),
            "agreement_claims": bool(data.get("agreement_claims", False)),
        },
    }
    return EvidenceSurfaceReport(
        jurisdiction=str(data.get("jurisdiction") or ""),
        report_kind=report_kind,
        schema="lawvm.frontend_phase_surface_report.v1",
        truth_claim=str(data.get("truth_claim") or "frontend phase diagnostics only"),
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        summary=summary,
        filters={
            "frontend": str(data.get("frontend") or ""),
            "source_hash": str(data.get("source_hash") or ""),
            "schema": str(data.get("schema") or ""),
        },
        filtered_summary=summary,
        rows=rows,
        rows_truncated=False,
        detail={
            "safe_default": "treat_frontend_phase_surface_as_parse_diagnostics_not_replay_authority",
            "forbidden_shortcuts": _FRONTEND_PHASE_FORBIDDEN_SHORTCUTS,
            "included_surfaces": ("frontend_phase_row", "frontend_diagnostic"),
            "frontend_surface_detail": _mapping(data.get("detail")),
        },
    )


def frontend_diagnostic_findings(
    diagnostics: tuple[FrontendDiagnostic, ...] | tuple[Mapping[str, Any], ...],
) -> tuple[Finding, ...]:
    """Project frontend diagnostics into governed findings.

    Human diagnostic strings may remain as compatibility/rendering fields. This
    projection gives phase-boundary consumers typed findings without treating
    diagnostics as replay authorization.
    """

    return tuple(_frontend_diagnostic_finding(row) for row in diagnostics)


def _frontend_diagnostic_finding(diagnostic: FrontendDiagnostic | Mapping[str, Any]) -> Finding:
    row = diagnostic.to_dict() if isinstance(diagnostic, FrontendDiagnostic) else dict(diagnostic)
    severity = str(row.get("severity") or "")
    blocking = bool(row.get("blocking"))
    if severity == "bug":
        kind = "PARSE.FRONTEND_INTERNAL_ERROR"
        role = "violation"
        finding_blocking = True
    elif blocking:
        kind = "PARSE.FRONTEND_BLOCKING_DIAGNOSTIC"
        role = "obligation"
        finding_blocking = True
    else:
        kind = "PARSE.FRONTEND_DIAGNOSTIC"
        role = "observation"
        finding_blocking = False
    return Finding(
        kind=kind,
        role=role,
        stage=str(row.get("phase") or "frontend_phase_surface"),
        blocking=finding_blocking,
        detail={
            "diagnostic_id": str(row.get("diagnostic_id") or ""),
            "frontend": str(row.get("frontend") or ""),
            "jurisdiction": str(row.get("jurisdiction") or ""),
            "severity": severity,
            "rule_id": str(row.get("rule_id") or ""),
            "message": str(row.get("message") or ""),
            "strict_disposition": str(row.get("strict_disposition") or "record"),
            "quirks_disposition": str(row.get("quirks_disposition") or "record"),
            "safe_default": str(row.get("safe_default") or "record_without_replay_authority"),
            "forbidden_shortcuts": tuple(
                str(item) for item in _sequence(row.get("forbidden_shortcuts"))
            ),
            "diagnostic_detail": _mapping(row.get("detail")),
        },
    )


def _phase_row_report_row(row: Mapping[str, Any], *, data: Mapping[str, Any]) -> dict[str, Any]:
    phase = str(row.get("phase") or "")
    return {
        "surface": "frontend_phase_row",
        "phase_status": str(row.get("phase_status") or "reported"),
        "subject_id": _phase_subject_id(data, phase),
        "source_ref": str(data.get("source_hash") or ""),
        "frontend": str(data.get("frontend") or ""),
        "phase": phase,
        "artifact_kind": str(row.get("artifact_kind") or ""),
        "authority_role": str(row.get("authority_role") or ""),
        "produced": bool(row.get("produced", False)),
        "input_artifacts": tuple(str(item) for item in _sequence(row.get("input_artifacts"))),
        "output_artifacts": tuple(str(item) for item in _sequence(row.get("output_artifacts"))),
        "diagnostic_ids": tuple(str(item) for item in _sequence(row.get("diagnostic_ids"))),
        "detail": _mapping(row.get("detail")),
        "replay_authorized": False,
        "forbidden_shortcuts": list(_FRONTEND_PHASE_FORBIDDEN_SHORTCUTS),
    }


def _diagnostic_report_row(row: Mapping[str, Any], *, data: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "surface": "frontend_diagnostic",
        "row_status": "blocking" if bool(row.get("blocking")) else str(row.get("severity") or "reported"),
        "row_id": str(row.get("diagnostic_id") or ""),
        "subject_id": _phase_subject_id(data, str(row.get("phase") or "diagnostic")),
        "source_ref": str(data.get("source_hash") or ""),
        "frontend": str(row.get("frontend") or data.get("frontend") or ""),
        "phase": str(row.get("phase") or ""),
        "severity": str(row.get("severity") or ""),
        "rule_id": str(row.get("rule_id") or ""),
        "message": str(row.get("message") or ""),
        "blocking": bool(row.get("blocking", False)),
        "strict_disposition": str(row.get("strict_disposition") or "record"),
        "quirks_disposition": str(row.get("quirks_disposition") or "record"),
        "safe_default": str(row.get("safe_default") or "record_without_replay_authority"),
        "detail": _mapping(row.get("detail")),
        "replay_authorized": False,
        "forbidden_shortcuts": tuple(
            dict.fromkeys(
                (
                    *tuple(str(item) for item in _sequence(row.get("forbidden_shortcuts"))),
                    *_FRONTEND_PHASE_FORBIDDEN_SHORTCUTS,
                )
            )
        ),
    }


def _phase_subject_id(data: Mapping[str, Any], phase: str) -> str:
    frontend = str(data.get("frontend") or "frontend")
    source_hash = str(data.get("source_hash") or "unknown-source")
    phase_slug = phase or "phase"
    return f"{frontend}:{source_hash}:{phase_slug}"


def _mapping_rows(value: Any) -> tuple[Mapping[str, Any], ...]:
    if isinstance(value, list | tuple):
        return tuple(item for item in value if isinstance(item, Mapping))
    return ()


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> tuple[Any, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, list | tuple):
        return tuple(value)
    return ()


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(value, 0)
    text = str(value or "")
    return int(text) if text.isdigit() else 0


def _required_string(field_name: str, value: Any) -> str:
    text = str(value or "")
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _string_tuple(field_name: str, values: tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(values, str):
        raise ValueError(f"{field_name} must be a tuple, not a string")
    try:
        return tuple(str(value) for value in values)
    except TypeError as exc:
        raise ValueError(f"{field_name} must be iterable") from exc


def _plain_jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_jsonable(inner) for key, inner in value.items()}
    if isinstance(value, list | tuple):
        return [_plain_jsonable(inner) for inner in value]
    if isinstance(value, set | frozenset):
        return sorted((_plain_jsonable(inner) for inner in value), key=repr)
    return value
