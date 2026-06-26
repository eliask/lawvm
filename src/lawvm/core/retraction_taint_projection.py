"""Retraction-taint projection — query-time rendering for the claim CLI.

Stored taint reports are stale-by-design; taint is a query-time projection
over the provenance graph (retracted attestations x consumed_by_build
edges).  This module computes that projection for a set of retracted
assertion ids and renders it for ``lawvm claim retract`` /
``lawvm claim taint-report``.  Nothing here persists anything.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Mapping

from lawvm.core.build_consumption import (
    BuildConsumptionStatus,
    BuildRecord,
    BuildTaintStatusFinding,
    _consumption_edges_by_build,
    build_consumption_status,
)
from lawvm.core.provenance_graph import ProvenanceAttestation, ProvenanceGraph


@dataclass(frozen=True, slots=True)
class ConsumingBuildProjection:
    """One build that consumed a retracted assertion, with its taint status."""

    status_finding: BuildTaintStatusFinding
    consumption_roles: tuple[str, ...]
    scope_summaries: tuple[str, ...]
    time_scope_summaries: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RetractionTaintProjection:
    """Query-time taint projection for a set of retracted assertion ids."""

    retracted_assertion_ids: tuple[str, ...]
    builds: tuple[ConsumingBuildProjection, ...]


def _compact(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def project_retraction_taint(
    graph: ProvenanceGraph,
    retracted_assertion_ids: tuple[str, ...],
    attestation_index: Mapping[str, ProvenanceAttestation],
    build_record_index: Mapping[str, BuildRecord],
) -> RetractionTaintProjection:
    """Project which builds are tainted by the given retracted assertions.

    Builds are discovered through consumed_by_build edges whose source is one
    of the retracted assertions; each discovered build is then put through
    the four-state status machine (structural pre-query validation included —
    an edge whose payload build_id disagrees with its destination raises).
    """
    edges_by_build = _consumption_edges_by_build(graph)
    retracted = set(retracted_assertion_ids)

    builds: list[ConsumingBuildProjection] = []
    for build_id in sorted(edges_by_build):
        consuming = [
            e for e in edges_by_build[build_id] if e.src_node_id in retracted
        ]
        if not consuming:
            continue
        status_finding = build_consumption_status(
            graph,
            build_id,
            attestation_index,
            build_record_index,
            _edges_by_build=edges_by_build,
        )
        builds.append(
            ConsumingBuildProjection(
                status_finding=status_finding,
                consumption_roles=tuple(
                    str(e.payload.get("consumption_role", "")) for e in consuming
                ),
                scope_summaries=tuple(
                    _compact(e.payload.get("scope", {})) for e in consuming
                ),
                time_scope_summaries=tuple(
                    _compact(e.payload.get("time_scope", {})) for e in consuming
                ),
            )
        )

    return RetractionTaintProjection(
        retracted_assertion_ids=tuple(sorted(retracted)),
        builds=tuple(builds),
    )


def filter_retraction_taint_projection_by_build(
    projection: RetractionTaintProjection,
    build_id: str,
) -> RetractionTaintProjection:
    """Return a projection narrowed to one consuming build id."""

    return RetractionTaintProjection(
        retracted_assertion_ids=projection.retracted_assertion_ids,
        builds=tuple(
            build
            for build in projection.builds
            if build.status_finding.build_id == build_id
        ),
    )


def render_retraction_taint(projection: RetractionTaintProjection) -> str:
    """Human-readable CLI rendering of the projection."""
    tainted = [
        b
        for b in projection.builds
        if b.status_finding.taint_status == BuildConsumptionStatus.TAINTED
    ]
    not_clean = [
        b
        for b in projection.builds
        if b.status_finding.taint_status
        not in (BuildConsumptionStatus.TAINTED, BuildConsumptionStatus.CLEAN)
    ]
    lines: list[str] = []
    if not projection.builds:
        lines.append(
            "taint report: no builds tainted "
            "(assertion not consumed by any instrumented build)"
        )
        return "\n".join(lines)

    lines.append(
        f"taint report: {len(tainted)} tainted build(s)"
        + (f", {len(not_clean)} build(s) not taint-checkable" if not_clean else "")
    )
    for build in projection.builds:
        sf = build.status_finding
        lines.append(f"  build: {sf.build_id}")
        lines.append(f"    status: {sf.taint_status.value}")
        if sf.detail:
            lines.append(f"    detail: {sf.detail}")
        for finding in sf.findings:
            lines.append(
                f"    retracted assertion: {finding.retracted_assertion_id[:32]}..."
            )
            lines.append(
                f"    retraction attestation: {finding.retraction_attestation_id[:32]}..."
            )
        for role, scope, time_scope in zip(
            build.consumption_roles,
            build.scope_summaries,
            build.time_scope_summaries,
            strict=True,
        ):
            lines.append(f"    consumption_role: {role}")
            lines.append(f"    scope: {scope}")
            lines.append(f"    time_scope: {time_scope}")
    return "\n".join(lines)
