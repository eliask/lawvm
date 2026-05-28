"""Shared replay artifact contracts.

API tier
--------
Stable cross-jurisdiction reporting/transport contract for replay summaries and
step records.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Mapping, Protocol

from lawvm.core.frozen_values import freeze_mapping


@dataclass(frozen=True)
class ReplayAmendmentStep:
    """One replay step in a jurisdiction-agnostic amendment chain."""

    source_id: str
    action: str = ""
    status: str = ""
    effective_date: str = ""
    op_count: int = 0
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty(self.source_id, "ReplayAmendmentStep.source_id")
        if self.status and not isinstance(self.status, str):
            raise ValueError("ReplayAmendmentStep.status must be a string")
        if self.op_count < 0:
            raise ValueError("ReplayAmendmentStep.op_count must be non-negative")
        if not isinstance(self.detail, Mapping):
            raise ValueError("ReplayAmendmentStep.detail must be a mapping")
        object.__setattr__(self, "detail", freeze_mapping(self.detail))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["detail"] = dict(self.detail)
        return data


@dataclass(frozen=True)
class ReplayTextView:
    """Optional rendered text payload attached to a replay summary."""

    format: str = "text/plain"
    content: str = ""

    def __post_init__(self) -> None:
        _require_non_empty(self.format, "ReplayTextView.format")
        if not isinstance(self.content, str):
            raise ValueError("ReplayTextView.content must be a string")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReplaySummary:
    """Shared top-level replay result contract."""

    jurisdiction: str
    base_id: str
    as_of: str
    title: str = ""
    status: str = "ok"
    error: str | None = None
    oracle_id: str = ""
    source_id: str = ""
    amendment_count: int = 0
    applied_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    op_count: int = 0
    consistent: bool | None = None
    divergence_count: int | None = None
    steps: tuple[ReplayAmendmentStep, ...] = ()
    text_view: ReplayTextView | None = None
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty(self.jurisdiction, "ReplaySummary.jurisdiction")
        _require_non_empty(self.base_id, "ReplaySummary.base_id")
        _require_non_empty(self.as_of, "ReplaySummary.as_of")
        _require_non_empty(self.status, "ReplaySummary.status")
        for field_name in ("amendment_count", "applied_count", "skipped_count", "failed_count", "op_count"):
            if getattr(self, field_name) < 0:
                raise ValueError(f"ReplaySummary.{field_name} must be non-negative")
        if self.divergence_count is not None and self.divergence_count < 0:
            raise ValueError("ReplaySummary.divergence_count must be non-negative")
        steps = tuple(self.steps)
        if not all(isinstance(step, ReplayAmendmentStep) for step in steps):
            raise ValueError("ReplaySummary.steps must contain ReplayAmendmentStep records")
        object.__setattr__(self, "steps", steps)
        if self.text_view is not None and not isinstance(self.text_view, ReplayTextView):
            raise ValueError("ReplaySummary.text_view must be a ReplayTextView")
        if not isinstance(self.detail, Mapping):
            raise ValueError("ReplaySummary.detail must be a mapping")
        object.__setattr__(self, "detail", freeze_mapping(self.detail))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["steps"] = [step.to_dict() for step in self.steps]
        data["text_view"] = self.text_view.to_dict() if self.text_view is not None else None
        data["detail"] = dict(self.detail)
        return data


# ---------------------------------------------------------------------------
# Replay checkpoint callback — generic version-drift / progress hook
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReplayCheckpoint:
    """Snapshot emitted after each amendment step during replay.

    Jurisdictions emit these from their amendment loop.  Consumers (e.g.
    version-drift detection, progress reporting) receive them via a
    ``ReplayCheckpointCallback``.

    ``serialize_text`` is a callable rather than a pre-computed string because
    serializing the full IR tree is expensive; callers that don't need text
    (e.g. progress bars) can ignore it.
    """

    parent_id: str
    amendment_id: str
    step_index: int
    total_steps: int
    serialize_text: Callable[[], str]

    def __post_init__(self) -> None:
        _require_non_empty(self.parent_id, "ReplayCheckpoint.parent_id")
        _require_non_empty(self.amendment_id, "ReplayCheckpoint.amendment_id")
        if self.step_index < 0:
            raise ValueError("ReplayCheckpoint.step_index must be non-negative")
        if self.total_steps < 0:
            raise ValueError("ReplayCheckpoint.total_steps must be non-negative")
        if self.total_steps and self.step_index >= self.total_steps:
            raise ValueError("ReplayCheckpoint.step_index must be less than total_steps")
        if not callable(self.serialize_text):
            raise ValueError("ReplayCheckpoint.serialize_text must be callable")


class ReplayCheckpointCallback(Protocol):
    """Protocol for replay checkpoint consumers.

    Return value is ignored by the replay loop — consumers accumulate
    state internally (e.g. best-match tracking for version drift).
    """

    def __call__(self, checkpoint: ReplayCheckpoint) -> None: ...


def _require_non_empty(value: str, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be non-empty")
