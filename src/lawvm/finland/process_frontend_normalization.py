"""Phase-2 frontend normalization wrapper for ``process_muutoslaki``.

This boundary keeps PEG/normalization output projection out of the process
orchestrator: normalized AmendmentOps, non-commence temporal events, observation
rows, and blocking findings are split once in a named phase object.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, List, Optional

from lxml import etree

from lawvm.core.compile_result import StrictProfile, TemporalEvent
from lawvm.core.ir import IRNode
from lawvm.core.phase_result import Finding
from lawvm.core.regex_recognition_coverage import RegexRecognitionCoverage
from lawvm.finland.johtolause import parse_clause as _parse_johtolause_clause
from lawvm.finland.ops import AmendmentOp
from lawvm.finland.temporal_rewrites import _normalize_frontend_temporal_events


@dataclass(frozen=True, slots=True)
class FrontendNormalizationResult:
    ops: tuple[AmendmentOp, ...]
    temporal_events: tuple[TemporalEvent, ...]
    elaboration_observations: tuple[dict[str, object], ...]
    process_findings: tuple[Finding, ...]


@dataclass(slots=True)
class ProcessFrontendNormalizationContext:
    johto: str
    muutos_tree: etree._Element
    state: Any
    base_ir: IRNode | None
    amendment_id: str
    source_title: str
    used_sec1_fallback: bool
    parent_id: str
    strict_profile: Optional[StrictProfile]
    regex_recognition_coverage_out: Optional[List[RegexRecognitionCoverage]]
    normalize_and_compile_ops: Callable[..., Any]

    def run(self) -> FrontendNormalizationResult:
        parse_result = _parse_johtolause_clause(self.johto)
        phase_result = self.normalize_and_compile_ops(
            johto=self.johto,
            muutos_tree=self.muutos_tree,
            master=self.state,
            base_ir=self.base_ir,
            amendment_id=self.amendment_id,
            source_title=self.source_title,
            used_sec1_fallback=self.used_sec1_fallback,
            parent_id=self.parent_id,
            strict_profile=self.strict_profile,
            parse_result=parse_result,
            regex_recognition_coverage_out=self.regex_recognition_coverage_out,
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
        findings = phase_result.findings()
        return FrontendNormalizationResult(
            ops=tuple(phase_result.output),
            temporal_events=tuple(temporal_events),
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
