"""Shared replay artifact contracts.

API tier
--------
Stable cross-jurisdiction reporting/transport contract for replay summaries and
step records.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Mapping, Protocol


@dataclass(frozen=True)
class ReplayAmendmentStep:
    """One replay step in a jurisdiction-agnostic amendment chain."""

    source_id: str
    action: str = ""
    status: str = ""
    effective_date: str = ""
    op_count: int = 0
    detail: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["detail"] = dict(self.detail)
        return data


@dataclass(frozen=True)
class ReplayTextView:
    """Optional rendered text payload attached to a replay summary."""

    format: str = "text/plain"
    content: str = ""

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


class ReplayCheckpointCallback(Protocol):
    """Protocol for replay checkpoint consumers.

    Return value is ignored by the replay loop — consumers accumulate
    state internally (e.g. best-match tracking for version drift).
    """

    def __call__(self, checkpoint: ReplayCheckpoint) -> None: ...
