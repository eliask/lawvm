"""Shared frontend capability declarations.

Frontend capability declarations are report/control-plane metadata. They say
which compiler waists a frontend surface exposes; they do not prove that a
particular parse result is replay-authorized and do not replace phase-surface
diagnostics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from lawvm.core.evidence_surface_report import EvidenceSurfaceReport
from lawvm.core.frozen_values import freeze_mapping


_FRONTEND_CAPABILITY_FORBIDDEN_SHORTCUTS: tuple[str, ...] = (
    "frontend_capability_as_replay_authorization",
    "frontend_capability_as_parse_success",
    "frontend_capability_as_canonical_effect_proof",
    "compatibility_output_as_semantic_authority",
)


@dataclass(frozen=True, slots=True)
class FrontendCapability:
    """Declare the phase waists a frontend surface supports."""

    frontend_id: str
    jurisdiction: str
    scope: str
    status: str
    capability_schema: str = "lawvm.frontend_capability.v1"
    has_token_tape: bool = False
    has_annotation_overlay: bool = False
    has_surface_clause: bool = False
    has_enriched_surface: bool = False
    has_resolved_surface: bool = False
    has_clause_ast: bool = False
    has_payload_surface: bool = False
    has_payload_elaboration: bool = False
    has_canonical_effects: bool = False
    has_replay_apply: bool = False
    has_materialization: bool = False
    has_agreement_surface: bool = False
    compatibility_outputs: tuple[str, ...] = ()
    phase_names: tuple[str, ...] = ()
    caveats: tuple[str, ...] = ()
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in ("frontend_id", "jurisdiction", "scope", "status", "capability_schema"):
            object.__setattr__(
                self,
                field_name,
                _required_string(f"FrontendCapability.{field_name}", getattr(self, field_name)),
            )
        for field_name in (
            "has_token_tape",
            "has_annotation_overlay",
            "has_surface_clause",
            "has_enriched_surface",
            "has_resolved_surface",
            "has_clause_ast",
            "has_payload_surface",
            "has_payload_elaboration",
            "has_canonical_effects",
            "has_replay_apply",
            "has_materialization",
            "has_agreement_surface",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise ValueError(f"FrontendCapability.{field_name} must be boolean")
        object.__setattr__(
            self,
            "compatibility_outputs",
            _string_tuple("FrontendCapability.compatibility_outputs", self.compatibility_outputs),
        )
        object.__setattr__(
            self,
            "phase_names",
            _string_tuple("FrontendCapability.phase_names", self.phase_names),
        )
        object.__setattr__(
            self,
            "caveats",
            _string_tuple("FrontendCapability.caveats", self.caveats),
        )
        if not isinstance(self.detail, Mapping):
            raise ValueError("FrontendCapability.detail must be a mapping")
        object.__setattr__(self, "detail", freeze_mapping(self.detail))

    def to_dict(self) -> dict[str, Any]:
        return {
            "frontend_id": self.frontend_id,
            "jurisdiction": self.jurisdiction,
            "scope": self.scope,
            "status": self.status,
            "capability_schema": self.capability_schema,
            "has_token_tape": self.has_token_tape,
            "has_annotation_overlay": self.has_annotation_overlay,
            "has_surface_clause": self.has_surface_clause,
            "has_enriched_surface": self.has_enriched_surface,
            "has_resolved_surface": self.has_resolved_surface,
            "has_clause_ast": self.has_clause_ast,
            "has_payload_surface": self.has_payload_surface,
            "has_payload_elaboration": self.has_payload_elaboration,
            "has_canonical_effects": self.has_canonical_effects,
            "has_replay_apply": self.has_replay_apply,
            "has_materialization": self.has_materialization,
            "has_agreement_surface": self.has_agreement_surface,
            "compatibility_outputs": list(self.compatibility_outputs),
            "phase_names": list(self.phase_names),
            "caveats": list(self.caveats),
            "detail": _plain_jsonable(self.detail),
        }


@dataclass(frozen=True, slots=True)
class SurfaceParseResult:
    """Report-facing surface-parse waist projection.

    This object records original/enriched/resolved surface status without
    importing frontend-local surface classes into core. It is not replay
    authority; it makes enrichment and resolver consumption visible.
    """

    frontend_id: str
    jurisdiction: str
    source_hash: str
    status: str
    original_surface_kind: str
    original_produced: bool
    enriched_surface_kind: str = ""
    enriched: bool = False
    resolved_surface_kind: str = ""
    resolved_produced: bool = False
    consumed_count: int = 0
    enrichment_rule_ids: tuple[str, ...] = ()
    supplementary_surface_kinds: tuple[str, ...] = ()
    diagnostic_ids: tuple[str, ...] = ()
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in (
            "frontend_id",
            "jurisdiction",
            "source_hash",
            "status",
            "original_surface_kind",
        ):
            object.__setattr__(
                self,
                field_name,
                _required_string(f"SurfaceParseResult.{field_name}", getattr(self, field_name)),
            )
        object.__setattr__(self, "enriched_surface_kind", str(self.enriched_surface_kind or ""))
        object.__setattr__(self, "resolved_surface_kind", str(self.resolved_surface_kind or ""))
        for field_name in ("original_produced", "enriched", "resolved_produced"):
            if not isinstance(getattr(self, field_name), bool):
                raise ValueError(f"SurfaceParseResult.{field_name} must be boolean")
        if not isinstance(self.consumed_count, int) or self.consumed_count < 0:
            raise ValueError("SurfaceParseResult.consumed_count must be a non-negative integer")
        object.__setattr__(
            self,
            "enrichment_rule_ids",
            _string_tuple("SurfaceParseResult.enrichment_rule_ids", self.enrichment_rule_ids),
        )
        object.__setattr__(
            self,
            "supplementary_surface_kinds",
            _string_tuple(
                "SurfaceParseResult.supplementary_surface_kinds",
                self.supplementary_surface_kinds,
            ),
        )
        object.__setattr__(
            self,
            "diagnostic_ids",
            _string_tuple("SurfaceParseResult.diagnostic_ids", self.diagnostic_ids),
        )
        if not isinstance(self.detail, Mapping):
            raise ValueError("SurfaceParseResult.detail must be a mapping")
        object.__setattr__(self, "detail", freeze_mapping(self.detail))

    def to_dict(self) -> dict[str, Any]:
        return {
            "frontend_id": self.frontend_id,
            "jurisdiction": self.jurisdiction,
            "source_hash": self.source_hash,
            "status": self.status,
            "original_surface_kind": self.original_surface_kind,
            "original_produced": self.original_produced,
            "enriched_surface_kind": self.enriched_surface_kind,
            "enriched": self.enriched,
            "resolved_surface_kind": self.resolved_surface_kind,
            "resolved_produced": self.resolved_produced,
            "consumed_count": self.consumed_count,
            "enrichment_rule_ids": list(self.enrichment_rule_ids),
            "supplementary_surface_kinds": list(self.supplementary_surface_kinds),
            "diagnostic_ids": list(self.diagnostic_ids),
            "detail": _plain_jsonable(self.detail),
        }


@dataclass(frozen=True, slots=True)
class DerivedCompatibilityArtifact:
    """A compatibility artifact derived from a primary frontend artifact.

    This is a report/control-plane certificate for legacy or transitional
    outputs such as Finland ``ParsedOp`` rows. It records derivation and loss
    boundaries; it does not make the compatibility artifact semantic authority
    and does not authorize replay.
    """

    artifact_id: str
    jurisdiction: str
    frontend_id: str
    artifact_kind: str
    source_artifact_id: str
    source_artifact_kind: str
    derivation_phase: str
    status: str
    lossy: bool
    preserved_fields: tuple[str, ...] = ()
    lost_fields: tuple[str, ...] = ()
    input_artifacts: tuple[str, ...] = ()
    output_artifacts: tuple[str, ...] = ()
    replay_authorized: bool = False
    semantic_authority: bool = False
    safe_default: str = "treat_compatibility_artifact_as_projection_not_authority"
    forbidden_shortcuts: tuple[str, ...] = (
        "compatibility_artifact_as_semantic_authority",
        "compatibility_artifact_as_replay_authorization",
        "compatibility_projection_as_canonical_effect_proof",
    )
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in (
            "artifact_id",
            "jurisdiction",
            "frontend_id",
            "artifact_kind",
            "source_artifact_id",
            "source_artifact_kind",
            "derivation_phase",
            "status",
            "safe_default",
        ):
            object.__setattr__(
                self,
                field_name,
                _required_string(
                    f"DerivedCompatibilityArtifact.{field_name}",
                    getattr(self, field_name),
                ),
            )
        for field_name in ("lossy", "replay_authorized", "semantic_authority"):
            if not isinstance(getattr(self, field_name), bool):
                raise ValueError(f"DerivedCompatibilityArtifact.{field_name} must be boolean")
        object.__setattr__(
            self,
            "preserved_fields",
            _string_tuple(
                "DerivedCompatibilityArtifact.preserved_fields",
                self.preserved_fields,
            ),
        )
        object.__setattr__(
            self,
            "lost_fields",
            _string_tuple("DerivedCompatibilityArtifact.lost_fields", self.lost_fields),
        )
        object.__setattr__(
            self,
            "input_artifacts",
            _string_tuple(
                "DerivedCompatibilityArtifact.input_artifacts",
                self.input_artifacts,
            ),
        )
        object.__setattr__(
            self,
            "output_artifacts",
            _string_tuple(
                "DerivedCompatibilityArtifact.output_artifacts",
                self.output_artifacts,
            ),
        )
        object.__setattr__(
            self,
            "forbidden_shortcuts",
            _string_tuple(
                "DerivedCompatibilityArtifact.forbidden_shortcuts",
                self.forbidden_shortcuts,
            ),
        )
        if not isinstance(self.detail, Mapping):
            raise ValueError("DerivedCompatibilityArtifact.detail must be a mapping")
        object.__setattr__(self, "detail", freeze_mapping(self.detail))

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "jurisdiction": self.jurisdiction,
            "frontend_id": self.frontend_id,
            "artifact_kind": self.artifact_kind,
            "source_artifact_id": self.source_artifact_id,
            "source_artifact_kind": self.source_artifact_kind,
            "derivation_phase": self.derivation_phase,
            "status": self.status,
            "lossy": self.lossy,
            "preserved_fields": list(self.preserved_fields),
            "lost_fields": list(self.lost_fields),
            "input_artifacts": list(self.input_artifacts),
            "output_artifacts": list(self.output_artifacts),
            "replay_authorized": self.replay_authorized,
            "semantic_authority": self.semantic_authority,
            "safe_default": self.safe_default,
            "forbidden_shortcuts": list(self.forbidden_shortcuts),
            "detail": _plain_jsonable(self.detail),
        }


def frontend_capability_evidence_report(
    capability: FrontendCapability | Mapping[str, Any],
    *,
    report_kind: str = "frontend_capability",
) -> EvidenceSurfaceReport:
    """Project a frontend capability declaration into a passive report row."""

    data = _frontend_capability_mapping(capability)
    supported_waists = tuple(
        field_name
        for field_name in (
            "has_token_tape",
            "has_annotation_overlay",
            "has_surface_clause",
            "has_enriched_surface",
            "has_resolved_surface",
            "has_clause_ast",
            "has_payload_surface",
            "has_payload_elaboration",
            "has_canonical_effects",
            "has_replay_apply",
            "has_materialization",
            "has_agreement_surface",
        )
        if bool(data.get(field_name))
    )
    row = {
        "surface": "frontend_capability",
        "row_id": str(data.get("frontend_id") or ""),
        "subject_id": str(data.get("frontend_id") or ""),
        "status": str(data.get("status") or "reported"),
        "replay_authorized": False,
        "semantic_authority": False,
        "supported_waists": supported_waists,
        "forbidden_shortcuts": _FRONTEND_CAPABILITY_FORBIDDEN_SHORTCUTS,
        **data,
    }
    summary = {
        "frontend_capability_count": 1,
        "frontend_id": str(data.get("frontend_id") or ""),
        "scope": str(data.get("scope") or ""),
        "status": str(data.get("status") or ""),
        "supported_waists": supported_waists,
        "supported_waist_count": len(supported_waists),
        "compatibility_outputs": tuple(str(item) for item in data.get("compatibility_outputs", ())),
        "phase_names": tuple(str(item) for item in data.get("phase_names", ())),
        "claim_flags": {
            "replay_claims": False,
            "canonical_effect_claims": False,
            "candidate_effect_claims": False,
            "dry_run_claims": False,
            "agreement_claims": False,
        },
    }
    return EvidenceSurfaceReport(
        jurisdiction=str(data.get("jurisdiction") or ""),
        report_kind=report_kind,
        schema="lawvm.frontend_capability_report.v1",
        truth_claim="frontend capability declaration",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        summary=summary,
        filters={
            "frontend_id": str(data.get("frontend_id") or ""),
            "scope": str(data.get("scope") or ""),
        },
        filtered_summary=summary,
        rows=(row,),
        rows_truncated=False,
        detail={
            "safe_default": "treat_capability_as_declaration_not_parse_or_replay_authority",
            "forbidden_shortcuts": _FRONTEND_CAPABILITY_FORBIDDEN_SHORTCUTS,
        },
    )


def _frontend_capability_mapping(capability: FrontendCapability | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(capability, FrontendCapability):
        return capability.to_dict()
    return FrontendCapability(
        frontend_id=str(capability.get("frontend_id") or ""),
        jurisdiction=str(capability.get("jurisdiction") or ""),
        scope=str(capability.get("scope") or ""),
        status=str(capability.get("status") or ""),
        capability_schema=str(capability.get("capability_schema") or "lawvm.frontend_capability.v1"),
        has_token_tape=bool(capability.get("has_token_tape")),
        has_annotation_overlay=bool(capability.get("has_annotation_overlay")),
        has_surface_clause=bool(capability.get("has_surface_clause")),
        has_enriched_surface=bool(capability.get("has_enriched_surface")),
        has_resolved_surface=bool(capability.get("has_resolved_surface")),
        has_clause_ast=bool(capability.get("has_clause_ast")),
        has_payload_surface=bool(capability.get("has_payload_surface")),
        has_payload_elaboration=bool(capability.get("has_payload_elaboration")),
        has_canonical_effects=bool(capability.get("has_canonical_effects")),
        has_replay_apply=bool(capability.get("has_replay_apply")),
        has_materialization=bool(capability.get("has_materialization")),
        has_agreement_surface=bool(capability.get("has_agreement_surface")),
        compatibility_outputs=tuple(str(item) for item in _sequence(capability.get("compatibility_outputs"))),
        phase_names=tuple(str(item) for item in _sequence(capability.get("phase_names"))),
        caveats=tuple(str(item) for item in _sequence(capability.get("caveats"))),
        detail=dict(capability.get("detail") or {}) if isinstance(capability.get("detail"), Mapping) else {},
    ).to_dict()


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


def _sequence(value: Any) -> tuple[Any, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, list | tuple):
        return tuple(value)
    return ()


def _plain_jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_jsonable(inner) for key, inner in value.items()}
    if isinstance(value, list | tuple):
        return [_plain_jsonable(inner) for inner in value]
    if isinstance(value, set | frozenset):
        return sorted((_plain_jsonable(inner) for inner in value), key=repr)
    return value
