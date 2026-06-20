"""Projection of compile-amendment PhaseResult signals for ``process_muutoslaki``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from lawvm.core.effect_lifecycle import EffectLifecycleEvent, EffectRef, EffectRelation
from lawvm.core.compile_result import SourcePathology
from lawvm.core.phase_result import Finding, PhaseResult
from lawvm.core.temporal import TemporalEvent
from lawvm.finland.effect_lifecycle_projection import build_finland_effect_lifecycle
from lawvm.finland.ops import ResolvedOp
from lawvm.finland.temporal_rewrites import _normalize_frontend_temporal_events


RecordProcessFinding = Callable[..., Finding]


@dataclass(slots=True)
class ProcessCompileSignalsContext:
    amendment_id: str
    parent_id: str
    resolved: list[ResolvedOp]
    compile_result: PhaseResult[list[ResolvedOp]]
    amendment_temporal_events: list[TemporalEvent]
    source_effects: list[EffectRef]
    effect_relations: list[EffectRelation]
    effect_lifecycle_events: list[EffectLifecycleEvent]
    source_pathologies: list[SourcePathology]
    elaboration_observations: list[dict[str, object]]
    sparse_slot_bindings: list[dict[str, object]]
    sparse_leftovers: list[dict[str, object]]
    process_findings: list[Finding]
    record_finding: RecordProcessFinding

    def project(self) -> None:
        temporal_events = _normalize_frontend_temporal_events(
            self.compile_result.temporal_events,
            amendment_id=self.amendment_id,
            target_statute=self.parent_id,
        )
        self.amendment_temporal_events.extend(temporal_events)
        self.source_effects.extend(self.compile_result.source_effects)
        self.effect_relations.extend(self.compile_result.effect_relations)
        self.effect_lifecycle_events.extend(self.compile_result.effect_lifecycle_events)
        source_effects, _relations, lifecycle_events = build_finland_effect_lifecycle(
            target_statute=self.parent_id,
            canonical_ops=(),
            temporal_events=temporal_events,
        )
        self.source_effects.extend(source_effects)
        self.effect_lifecycle_events.extend(lifecycle_events)
        self._cover_temporal_coverage()
        self._project_observations()
        self._project_obligations_and_violations()

    def _cover_temporal_coverage(self) -> None:
        """Emit bounded, non-blocking telemetry for missing johto-level temporal coverage."""
        fi_johto_prefix = "finland-johto:"

        structural_groups: set[str] = set()
        for op in self.resolved:
            source_statute_for_group = (
                op.resolved_source_statute
                or op.op.source_statute
                or self.amendment_id
            )
            structural_groups.add(f"{fi_johto_prefix}{source_statute_for_group}")

        temporal_groups: set[str] = set()
        for event in self.compile_result.temporal_events:
            group_id = event.group_id or ""
            if group_id.startswith(fi_johto_prefix):
                temporal_groups.add(group_id)

        missing_groups = tuple(sorted(structural_groups - temporal_groups))
        if not structural_groups or not missing_groups:
            return

        self.record_finding(
            kind="TIME.TRIGGER_COVERAGE_INCOMPLETE",
            role="obligation",
            blocking=True,
            message=(
                "Temporal authority is missing for one or more Finland johto-grouped "
                "structural operations and will remain a migration fallback for this "
                "compile path."
            ),
            source_statute=self.amendment_id,
            detail={
                "coverage_prefix": fi_johto_prefix,
                "missing_group_ids": list(missing_groups),
                "structural_group_count": len(structural_groups),
                "temporal_group_count": len(temporal_groups),
            },
        )

    def _project_observations(self) -> None:
        for finding in self.compile_result.findings():
            if finding.role == "observation" and finding.kind == "ELAB.SOURCE_PATHOLOGY":
                detail = dict(finding.detail)
                if "target_kind" in detail:
                    detail = dict(detail)
                    detail.pop("target_kind", None)
                self.source_pathologies.append(
                    SourcePathology.from_internal_detail(
                        source_statute=finding.source_statute,
                        detail=detail,
                    )
                )
            elif finding.role == "observation" and finding.kind == "ELAB.SPARSE_SLOT_BINDING":
                self.sparse_slot_bindings.append(dict(finding.detail))
            elif (
                finding.role == "observation"
                and finding.kind not in (
                    "ELAB.SOURCE_PATHOLOGY",
                    "ELAB.SPARSE_SLOT_BINDING",
                    "ELAB.MISSING_PAYLOAD_SURFACE",
                )
            ):
                self.elaboration_observations.append(dict(finding.detail))

    def _project_obligations_and_violations(self) -> None:
        for finding in self.compile_result.findings():
            if finding.role == "violation":
                # Carry compile-rail violations verbatim; re-wrapping through
                # record_finding would rewrite their role and lose provenance.
                self.process_findings.append(finding)
                continue
            if finding.role != "obligation":
                continue
            if finding.kind == "ELAB.SPARSE_PAYLOAD_LEFTOVER" and not finding.blocking:
                self.sparse_leftovers.append(dict(finding.detail))
            elif finding.blocking:
                detail = dict(finding.detail)
                self.record_finding(
                    kind=finding.kind,
                    message=str(detail.get("message", "")),
                    source_statute=str(detail.get("source_statute", "")),
                    detail={k: v for k, v in detail.items() if k not in ("message", "source_statute")},
                )
