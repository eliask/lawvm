"""Shared frontend capability declarations.

Frontend capability declarations are report/control-plane metadata. They say
which compiler waists a frontend surface exposes; they do not prove that a
particular parse result is replay-authorized and do not replace phase-surface
diagnostics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from lawvm.core.frozen_values import freeze_mapping


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
