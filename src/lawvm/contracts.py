"""Shared cross-cutting contracts that sit above the semantic kernel.

These are not kernel semantics themselves. They describe how products report
status, degradation, and artifact identity across core, analysis, and tooling.

API tier
--------
Stable cross-cutting product/reporting contract. These wrappers exist above the
semantic kernel and are intended for persisted/published artifact boundaries.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, Literal, TypeVar


StatusKind = Literal["complete", "partial", "blocked", "failed"]


@dataclass(frozen=True)
class ProcessingStatus:
    """Machine-readable product status for complete/partial/blocked/failed flows."""

    kind: StatusKind
    blockers: tuple[str, ...] = ()

    @property
    def is_degraded(self) -> bool:
        return self.kind != "complete"

    def to_jsonable_dict(self) -> dict[str, Any]:
        """Return the wire shape for a processing status."""
        return {
            "kind": self.kind,
            "blockers": list(self.blockers),
        }


T = TypeVar("T")


@dataclass(frozen=True)
class ArtifactEnvelope(Generic[T]):
    """Versioned wrapper for persisted or published artifacts."""

    schema: str
    producer: str
    version: str
    payload: T
    status: ProcessingStatus = field(default_factory=lambda: ProcessingStatus(kind="complete"))

    def to_jsonable_dict(self) -> dict[str, Any]:
        """Return the wire shape for a persisted artifact envelope."""
        return {
            "schema": self.schema,
            "producer": self.producer,
            "version": self.version,
            "payload": to_wire_jsonable(self.payload),
            "status": self.status.to_jsonable_dict(),
        }


def to_wire_jsonable(value: Any) -> Any:
    """Normalize a value into a JSON-friendly wire shape.

    This is intentionally conservative for persisted/reporting boundaries:
    unsupported runtime objects are converted to ``repr(value)`` rather than
    leaking arbitrary in-memory shapes into supposedly stable artifacts.

    If a value exposes ``to_jsonable_dict()``, that is the preferred wire hook
    and takes precedence over generic container coercion.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    to_jsonable_dict = getattr(value, "to_jsonable_dict", None)
    if callable(to_jsonable_dict):
        return to_wire_jsonable(to_jsonable_dict())
    if isinstance(value, dict):
        return {str(key): to_wire_jsonable(inner) for key, inner in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_wire_jsonable(inner) for inner in value]
    return repr(value)
