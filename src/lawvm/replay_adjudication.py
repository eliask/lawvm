"""Jurisdiction-facing adjudication projection models.

The core boundary exports canonical semantic contracts (bundle, finding, and
temporal models). These adjudication records are frontend-facing projection
models for replay summaries and should stay outside core.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from lawvm.core.frozen_values import freeze_mapping


@dataclass(frozen=True)
class SourceAdjudication:
    """Typed source/oracle comparison summary emitted by a replay frontend."""

    statute_id: str
    replay_mode: str
    cutoff_date: str = ""
    oracle_version_amendment_id: str = ""
    oracle_suspect: str = ""
    html_noncommensurable_reason: str = ""
    lineage: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "lineage",
            tuple(freeze_mapping(row) for row in self.lineage),
        )


@dataclass(frozen=True)
class CompileAdjudication:
    """Interop adjudication record for frontend replay surfaces.

    ``blocking`` and ``phase`` are enforcement-significant and must be supplied
    by the emitting frontend. They are intentionally non-defaulting: the
    emitter knows whether a finding blocks and which phase produced it. Reading
    them from the untyped ``detail`` payload with a permissive default would
    let an emitter that forgot to classify a finding silently inherit
    ``blocking=True`` / a substring-guessed phase, which is the boundary leak
    this carrier closes.
    """

    kind: str
    message: str
    source_statute: str
    blocking: bool
    phase: str
    op_id: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.blocking, bool):
            raise TypeError(
                f"CompileAdjudication.blocking must be a bool, got {type(self.blocking)!r}"
            )
        if not self.phase:
            raise ValueError("CompileAdjudication requires a non-empty phase")
        object.__setattr__(self, "detail", freeze_mapping(self.detail))


__all__ = [
    "SourceAdjudication",
    "CompileAdjudication",
]
