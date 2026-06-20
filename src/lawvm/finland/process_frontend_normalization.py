"""Phase-2 frontend normalization wrapper for ``process_muutoslaki``.

This boundary keeps PEG/normalization output projection out of the process
orchestrator: normalized AmendmentOps, non-commence temporal events, observation
rows, and blocking findings are split once in a named phase object.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, List, Optional

from lawvm.core.effect_lifecycle import EffectLifecycleEvent, EffectRef, EffectRelation
from lawvm.core.compile_result import StrictProfile
from lawvm.core.ir import IRNode
from lawvm.core.phase_result import Finding
from lawvm.core.regex_recognition_coverage import RegexRecognitionCoverage
from lawvm.core.temporal import TemporalEvent
from lawvm.finland.johtolause import parse_clause as _parse_johtolause_clause
from lawvm.finland.effect_lifecycle_projection import build_finland_effect_lifecycle
from lawvm.finland.effect_graph_merge import (
    append_unique_effect_lifecycle_events,
    append_unique_effect_refs,
    append_unique_effect_relations,
)
from lawvm.finland.ops import AmendmentOp
from lawvm.finland.source_model import AmendmentSourceModel
from lawvm.finland.temporal_rewrites import _normalize_frontend_temporal_events


@dataclass(frozen=True, slots=True)
class FrontendNormalizationResult:
    ops: tuple[AmendmentOp, ...]
    temporal_events: tuple[TemporalEvent, ...]
    source_effects: tuple[EffectRef, ...]
    effect_relations: tuple[EffectRelation, ...]
    effect_lifecycle_events: tuple[EffectLifecycleEvent, ...]
    elaboration_observations: tuple[dict[str, object], ...]
    process_findings: tuple[Finding, ...]


@dataclass(slots=True)
class ProcessFrontendNormalizationContext:
    johto: str
    source_model: AmendmentSourceModel
    state: Any
    base_ir: IRNode | None
    amendment_id: str
    source_title: str
    used_sec1_fallback: bool
    parent_id: str
    strict_profile: Optional[StrictProfile]
    regex_recognition_coverage_out: Optional[List[RegexRecognitionCoverage]]
    normalize_and_compile_ops: Callable[..., Any]
    amendment_metadata: Any = None

    def run(self) -> FrontendNormalizationResult:
        parse_result = _parse_johtolause_clause(self.johto)
        phase_result = self.source_model.normalize_and_compile_ops(
            compile_ops=self.normalize_and_compile_ops,
            johto=self.johto,
            master=self.state,
            base_ir=self.base_ir,
            amendment_id=self.amendment_id,
            source_title=self.source_title,
            used_sec1_fallback=self.used_sec1_fallback,
            parent_id=self.parent_id,
            strict_profile=self.strict_profile,
            parse_result=parse_result,
            regex_recognition_coverage_out=self.regex_recognition_coverage_out,
            amendment_metadata=self.amendment_metadata,
        )
        non_commence_events = tuple(
            event
            for event in phase_result.temporal_events
            if event.kind != "commence"
        )
        temporal_events = (
            _normalize_frontend_temporal_events(
                non_commence_events,
                amendment_id=self.amendment_id,
                target_statute=self.parent_id,
            )
            if non_commence_events
            else ()
        )
        derived_source_effects, _relations, derived_lifecycle_events = build_finland_effect_lifecycle(
            target_statute=self.parent_id,
            canonical_ops=(),
            temporal_events=temporal_events,
        )
        source_effects: list[EffectRef] = []
        effect_relations: list[EffectRelation] = []
        lifecycle_events: list[EffectLifecycleEvent] = []
        append_unique_effect_refs(
            source_effects,
            phase_result.source_effects,
            subject="frontend normalization phase source effects",
        )
        append_unique_effect_relations(
            effect_relations,
            phase_result.effect_relations,
            subject="frontend normalization phase effect relations",
        )
        append_unique_effect_lifecycle_events(
            lifecycle_events,
            phase_result.effect_lifecycle_events,
            subject="frontend normalization phase lifecycle events",
        )
        append_unique_effect_refs(
            source_effects,
            derived_source_effects,
            subject="frontend normalization source effects",
        )
        append_unique_effect_lifecycle_events(
            lifecycle_events,
            derived_lifecycle_events,
            subject="frontend normalization lifecycle events",
        )
        findings = phase_result.findings()
        return FrontendNormalizationResult(
            ops=tuple(phase_result.output),
            temporal_events=tuple(temporal_events),
            source_effects=tuple(source_effects),
            effect_relations=tuple(effect_relations),
            effect_lifecycle_events=tuple(lifecycle_events),
            elaboration_observations=tuple(
                dict(finding.detail)
                for finding in findings
                if finding.role == "observation"
            ),
            process_findings=tuple(
                finding
                for finding in findings
                if finding.role in ("obligation", "violation")
            ),
        )
