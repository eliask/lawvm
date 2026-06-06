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

from lawvm.core.frozen_values import freeze_mapping


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
    quirks_disposition: str = "record"
    safe_default: str = "record_without_replay_authority"
    forbidden_shortcuts: tuple[str, ...] = ()
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in (
            "diagnostic_id",
            "jurisdiction",
            "frontend",
            "phase",
            "severity",
            "rule_id",
            "message",
            "strict_disposition",
            "quirks_disposition",
            "safe_default",
        ):
            object.__setattr__(
                self,
                field_name,
                _required_string(f"FrontendDiagnostic.{field_name}", getattr(self, field_name)),
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
    status: str
    artifact_kind: str
    authority_role: str
    produced: bool
    input_artifacts: tuple[str, ...] = ()
    output_artifacts: tuple[str, ...] = ()
    diagnostic_ids: tuple[str, ...] = ()
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in ("phase", "status", "artifact_kind", "authority_role"):
            object.__setattr__(
                self,
                field_name,
                _required_string(f"FrontendPhaseRow.{field_name}", getattr(self, field_name)),
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
            "status": self.status,
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
        for field_name in ("jurisdiction", "frontend", "schema", "truth_claim", "source_hash"):
            object.__setattr__(
                self,
                field_name,
                _required_string(f"FrontendPhaseSurface.{field_name}", getattr(self, field_name)),
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
        for field_name in (
            "replay_claims",
            "canonical_effect_claims",
            "dry_run_claims",
            "agreement_claims",
        ):
            if not isinstance(getattr(self, field_name), bool):
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
