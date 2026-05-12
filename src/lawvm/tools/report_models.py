"""Typed in-process report payloads for tool surfaces.

These are the structured companions to the JSON/dict wire payloads used by
debug and divergence reporting commands.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class NorwayTraceSourceRow:
    source_id: str
    effective_status: str
    title: str
    compiled_op_count: int = 0
    matched_op_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "effective_status": self.effective_status,
            "title": self.title,
            "compiled_op_count": self.compiled_op_count,
            "matched_op_count": self.matched_op_count,
        }


@dataclass(frozen=True)
class NorwayTraceOpRow:
    source_id: str
    sequence: int
    action: str
    target_text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "sequence": self.sequence,
            "action": self.action,
            "target_text": self.target_text,
        }


@dataclass(frozen=True)
class NorwayDivergenceItem:
    address: tuple[tuple[str, str], ...]
    address_text: str
    divergence_type: str
    hint: str
    ops_text: str
    consolidated_text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "address": [list(pair) for pair in self.address],
            "address_text": self.address_text,
            "divergence_type": self.divergence_type,
            "hint": self.hint,
            "ops_text": self.ops_text,
            "consolidated_text": self.consolidated_text,
        }


@dataclass(frozen=True)
class NorwayCompareProjectionItem:
    surface: str
    rule_id: str
    reason: str
    address: tuple[tuple[str, str], ...]
    before_kind: str
    before_label: str | None
    before_text: str
    after_text: str
    before_child_count: int
    after_child_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface": self.surface,
            "rule_id": self.rule_id,
            "family": "editorial_projection",
            "phase": "oracle_compare",
            "reason": self.reason,
            "address": [list(pair) for pair in self.address],
            "before_kind": self.before_kind,
            "before_label": self.before_label,
            "before_text": self.before_text,
            "after_text": self.after_text,
            "before_child_count": self.before_child_count,
            "after_child_count": self.after_child_count,
        }


@dataclass(frozen=True)
class NorwayDivergencePayload:
    base_id: str
    as_of: str
    current_title: str
    replay_status: str
    consistent: bool
    overall_hint: str
    divergence_count: int
    divergence_counts: Mapping[str, int] = field(default_factory=dict)
    raw_divergence_count: int = 0
    raw_divergence_counts: Mapping[str, int] = field(default_factory=dict)
    compare_projection_count: int = 0
    compare_projection_rule_counts: Mapping[str, int] = field(default_factory=dict)
    indexed_amendment_count: int = 0
    applied_amendment_count: int = 0
    replay_op_count: int = 0
    source_signal: str = ""
    error: str = ""
    touched_divergence_count: int = 0
    untouched_divergence_count: int = 0
    divergences: tuple[NorwayDivergenceItem, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_id": self.base_id,
            "as_of": self.as_of,
            "current_title": self.current_title,
            "replay_status": self.replay_status,
            "consistent": self.consistent,
            "overall_hint": self.overall_hint,
            "divergence_count": self.divergence_count,
            "divergence_counts": dict(self.divergence_counts),
            "raw_divergence_count": self.raw_divergence_count,
            "raw_divergence_counts": dict(self.raw_divergence_counts),
            "compare_projection_count": self.compare_projection_count,
            "compare_projection_rule_counts": dict(self.compare_projection_rule_counts),
            "indexed_amendment_count": self.indexed_amendment_count,
            "applied_amendment_count": self.applied_amendment_count,
            "replay_op_count": self.replay_op_count,
            "source_signal": self.source_signal,
            "error": self.error,
            "touched_divergence_count": self.touched_divergence_count,
            "untouched_divergence_count": self.untouched_divergence_count,
            "divergences": [item.to_dict() for item in self.divergences],
        }


@dataclass(frozen=True)
class NorwayDebugPayload:
    base_id: str
    as_of: str
    title: str
    replay_status: str
    executable_replay_status: str
    consistent: bool
    overall_hint: str
    divergence_count: int
    divergence_counts: Mapping[str, int] = field(default_factory=dict)
    raw_divergence_count: int = 0
    raw_divergence_counts: Mapping[str, int] = field(default_factory=dict)
    compare_projection_count: int = 0
    compare_projection_rule_counts: Mapping[str, int] = field(default_factory=dict)
    indexed_amendment_count: int = 0
    applied_amendment_count: int = 0
    replay_op_count: int = 0
    source_signal: str = ""
    error: str = ""
    amendment_count: int = 0
    blocking_count: int = 0
    blocking_ops: int = 0
    source_count: int = 0
    matched_source_count: int = 0
    op_count: int = 0
    touched_divergence_count: int = 0
    untouched_divergence_count: int = 0
    path_filters: tuple[str, ...] = ()
    sources: tuple[NorwayTraceSourceRow, ...] = ()
    ops: tuple[NorwayTraceOpRow, ...] = ()
    divergences: tuple[NorwayDivergenceItem, ...] = ()
    compare_projections: tuple[NorwayCompareProjectionItem, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_id": self.base_id,
            "as_of": self.as_of,
            "title": self.title,
            "replay_status": self.replay_status,
            "executable_replay_status": self.executable_replay_status,
            "consistent": self.consistent,
            "overall_hint": self.overall_hint,
            "divergence_count": self.divergence_count,
            "divergence_counts": dict(self.divergence_counts),
            "raw_divergence_count": self.raw_divergence_count,
            "raw_divergence_counts": dict(self.raw_divergence_counts),
            "compare_projection_count": self.compare_projection_count,
            "compare_projection_rule_counts": dict(self.compare_projection_rule_counts),
            "indexed_amendment_count": self.indexed_amendment_count,
            "applied_amendment_count": self.applied_amendment_count,
            "replay_op_count": self.replay_op_count,
            "source_signal": self.source_signal,
            "error": self.error,
            "amendment_count": self.amendment_count,
            "blocking_count": self.blocking_count,
            "blocking_ops": self.blocking_ops,
            "source_count": self.source_count,
            "matched_source_count": self.matched_source_count,
            "op_count": self.op_count,
            "touched_divergence_count": self.touched_divergence_count,
            "untouched_divergence_count": self.untouched_divergence_count,
            "path_filters": list(self.path_filters),
            "sources": [item.to_dict() for item in self.sources],
            "ops": [item.to_dict() for item in self.ops],
            "divergences": [item.to_dict() for item in self.divergences],
            "compare_projections": [item.to_dict() for item in self.compare_projections],
        }
