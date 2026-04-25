"""Typed in-process payloads for capture/disagreement reporting."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping
import warnings


class _DictCompatMixin:
    """Read-only dict-style compatibility over typed payload views."""

    def to_dict(self) -> dict[str, Any]:
        raise NotImplementedError

    def _warn_dict_compat(self) -> None:
        warnings.warn(
            f"{type(self).__name__} dict-style access is transitional; use typed attributes instead.",
            UserWarning,
            stacklevel=3,
        )

    def __getitem__(self, key: str) -> Any:
        self._warn_dict_compat()
        return self.to_dict()[key]

    def get(self, key: str, default: Any = None) -> Any:
        self._warn_dict_compat()
        return self.to_dict().get(key, default)

    def __iter__(self) -> Iterator[str]:
        self._warn_dict_compat()
        return iter(self.to_dict())

    def __len__(self) -> int:
        self._warn_dict_compat()
        return len(self.to_dict())


@dataclass(frozen=True)
class CaptureBodyShapeView(_DictCompatMixin):
    body_intro_excerpt: str = ""
    parts: tuple[str, ...] = ()
    chapters: tuple[str, ...] = ()
    sections: tuple[str, ...] = ()
    part_count: int = 0
    chapter_count: int = 0
    section_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "body_intro_excerpt": self.body_intro_excerpt,
            "parts": list(self.parts),
            "chapters": list(self.chapters),
            "sections": list(self.sections),
            "part_count": self.part_count,
            "chapter_count": self.chapter_count,
            "section_count": self.section_count,
        }


@dataclass(frozen=True)
class CaptureSourceCompletenessView(_DictCompatMixin):
    chain_length: int = 0
    source_available: int = 0
    dates_available: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain_length": self.chain_length,
            "source_available": self.source_available,
            "dates_available": self.dates_available,
        }


@dataclass(frozen=True)
class CaptureSourcePathologyView(_DictCompatMixin):
    code: str
    message: str
    source_statute: str
    target_label: str
    detail: Mapping[str, Any] = field(default_factory=dict)
    target_unit_kind: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "source_statute": self.source_statute,
            "target_unit_kind": self.target_unit_kind,
            "target_label": self.target_label,
            "detail": dict(self.detail),
        }


@dataclass(frozen=True)
class CaptureSourceAdjudicationView(_DictCompatMixin):
    statute_id: str
    replay_mode: str
    cutoff_date: str
    oracle_version_amendment_id: str
    oracle_suspect: str
    html_noncommensurable_reason: str = ""
    source_pathologies: tuple[CaptureSourcePathologyView, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "statute_id": self.statute_id,
            "replay_mode": self.replay_mode,
            "cutoff_date": self.cutoff_date,
            "oracle_version_amendment_id": self.oracle_version_amendment_id,
            "oracle_suspect": self.oracle_suspect,
            "html_noncommensurable_reason": self.html_noncommensurable_reason,
            "source_pathologies": [item.to_dict() for item in self.source_pathologies],
        }


@dataclass(frozen=True)
class CaptureReplayMetaView(_DictCompatMixin):
    cutoff_date: str = ""
    oracle_version_amendment_id: str = ""
    oracle_suspect: str = ""
    elaboration_observations_count: int = 0
    payload_completeness_count: int = 0
    payload_completeness_kind_counts: Mapping[str, int] = field(default_factory=dict)
    payload_completeness_tail_policy_counts: Mapping[str, int] = field(default_factory=dict)
    sparse_slot_bindings_count: int = 0
    sparse_leftovers_count: int = 0
    apply_mutation_events_count: int = 0
    apply_mutation_invariant_reports_count: int = 0
    apply_mutation_invariant_result_code_counts: Mapping[str, int] = field(default_factory=dict)
    elaboration_observations: tuple[dict[str, Any], ...] = ()
    sparse_slot_bindings: tuple[dict[str, Any], ...] = ()
    sparse_leftovers: tuple[dict[str, Any], ...] = ()
    apply_mutation_events: tuple[dict[str, Any], ...] = ()
    apply_mutation_invariant_reports: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "cutoff_date": self.cutoff_date,
            "oracle_version_amendment_id": self.oracle_version_amendment_id,
            "oracle_suspect": self.oracle_suspect,
            "elaboration_observations_count": self.elaboration_observations_count,
            "payload_completeness_count": self.payload_completeness_count,
            "payload_completeness_kind_counts": dict(self.payload_completeness_kind_counts),
            "payload_completeness_tail_policy_counts": dict(self.payload_completeness_tail_policy_counts),
            "sparse_slot_bindings_count": self.sparse_slot_bindings_count,
            "sparse_leftovers_count": self.sparse_leftovers_count,
            "apply_mutation_events_count": self.apply_mutation_events_count,
            "apply_mutation_invariant_reports_count": self.apply_mutation_invariant_reports_count,
            "apply_mutation_invariant_result_code_counts": dict(self.apply_mutation_invariant_result_code_counts),
            "elaboration_observations": list(self.elaboration_observations),
            "sparse_slot_bindings": list(self.sparse_slot_bindings),
            "sparse_leftovers": list(self.sparse_leftovers),
            "apply_mutation_events": list(self.apply_mutation_events),
            "apply_mutation_invariant_reports": list(self.apply_mutation_invariant_reports),
        }


@dataclass(frozen=True)
class CaptureCountsView(_DictCompatMixin):
    compiled_ops: int = 0
    canonical_ops: int = 0
    failed_ops: int = 0
    projection_rows: int = 0
    amendments: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "compiled_ops": self.compiled_ops,
            "canonical_ops": self.canonical_ops,
            "failed_ops": self.failed_ops,
            "projection_rows": self.projection_rows,
            "amendments": self.amendments,
        }


@dataclass(frozen=True)
class CaptureAmendmentView(_DictCompatMixin):
    statute_id: str
    title: str
    issue_date: str
    effective_date: str
    included: bool
    source_available: bool
    body_shape: CaptureBodyShapeView | None = None
    counts: CaptureCountsView | None = None
    compiled_ops: tuple[dict[str, Any], ...] = ()
    canonical_ops: tuple[dict[str, Any], ...] = ()
    failed_ops: tuple[dict[str, Any], ...] = ()
    projection_rows: tuple[dict[str, Any], ...] = ()
    source_pathologies: tuple[dict[str, Any], ...] = ()
    apply_mutation_invariant_reports: tuple[dict[str, Any], ...] = ()
    apply_mutation_invariant_result_code_counts: Mapping[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "statute_id": self.statute_id,
            "title": self.title,
            "issue_date": self.issue_date,
            "effective_date": self.effective_date,
            "included": self.included,
            "source_available": self.source_available,
            "body_shape": self.body_shape.to_dict() if self.body_shape is not None else None,
            "counts": self.counts.to_dict() if self.counts is not None else None,
            "compiled_ops": list(self.compiled_ops),
            "canonical_ops": list(self.canonical_ops),
            "failed_ops": list(self.failed_ops),
            "projection_rows": list(self.projection_rows),
            "source_pathologies": list(self.source_pathologies),
            "apply_mutation_invariant_reports": list(self.apply_mutation_invariant_reports),
            "apply_mutation_invariant_result_code_counts": dict(self.apply_mutation_invariant_result_code_counts),
        }


@dataclass(frozen=True)
class CapturePayload(_DictCompatMixin):
    statute_id: str
    replay_mode: str
    compile_mode: str
    profile: str
    source_completeness: CaptureSourceCompletenessView | None = None
    source_adjudication: CaptureSourceAdjudicationView | None = None
    replay_meta: CaptureReplayMetaView | None = None
    counts: CaptureCountsView | None = None
    top_level_projection_rows: tuple[dict[str, Any], ...] = ()
    amendments: tuple[CaptureAmendmentView, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "statute_id": self.statute_id,
            "replay_mode": self.replay_mode,
            "compile_mode": self.compile_mode,
            "profile": self.profile,
            "source_completeness": (
                self.source_completeness.to_dict() if self.source_completeness is not None else None
            ),
            "source_adjudication": (
                self.source_adjudication.to_dict() if self.source_adjudication is not None else None
            ),
            "replay_meta": self.replay_meta.to_dict() if self.replay_meta is not None else None,
            "counts": self.counts.to_dict() if self.counts is not None else None,
            "top_level_projection_rows": list(self.top_level_projection_rows),
            "amendments": [item.to_dict() for item in self.amendments],
        }
