"""H3 temporal/applicability surface lens (Pro r5 Phase 6 — nodes only).

Adapts the deterministic :func:`lawvm.finland.references.temporal.recognize_temporal_exprs`
recognizer into a :class:`lawvm.core.legal_surface_lens.SurfaceLens`. It emits one
``temporal_expr`` surface node per recognised :class:`TemporalExpr` and NO edges:
Pro r5 Phase 6 is "temporal and actor/modal nodes as graph facts — no cross-lens
semantics yet" (a temporal cue is NOT yet attached to whatever it qualifies).

SCOPE BOUNDARY (mirrors the recognizer's own contract): this is a SURFACE lens.
Each node is a typed surface fact + source span. It does NOT authorize replay and
is NOT the expiry/commencement engine — a ``COMMENCEMENT`` cue states "the text
speaks of entry into force", never a state change.

Span alignment (Pro r5 §"span alignment"): the recognizer takes a TEXT string and
reports offsets relative to THAT text. We feed it ``unit.raw_text`` verbatim, so
its ``SourceSpan.byte_offset``/``byte_len`` are already raw_text-relative CHARACTER
offsets (Python ``re`` indexes characters, not bytes). We therefore build the
``SourceSpanRef`` DIRECTLY from those offsets — more precise than re-locating via
``locate_span`` (which would re-find the first/next occurrence of the surface
text and could disagree with the recognizer for a repeated surface). ``locate_span``
remains the fallback only for recognizers that do not expose offsets; this one does.
"""
from __future__ import annotations

import hashlib
from typing import Mapping

from lawvm.core.legal_surface_graph import ResolutionStatus, SourceSpanRef
from lawvm.core.legal_surface_lens import (
    SourceSurfaceBundle,
    SurfaceAnalysisContext,
    SurfaceLensResult,
    SurfaceNodeSeed,
)
from lawvm.finland.references.temporal import (
    TemporalExpr,
    TemporalStatus,
    recognize_temporal_exprs,
)

_LENS_ID = "fi.temporal.v0"

# TemporalStatus → ResolutionStatus (Pro r5 Phase 6 mapping). EVENT_BOUND and OPEN
# both collapse to the surface-graph "open" resolution status; only a concretely
# parsed bound is "resolved"; an untypeable cue is "unsupported".
_STATUS_MAP: Mapping[TemporalStatus, ResolutionStatus] = {
    TemporalStatus.RESOLVED: "resolved",
    TemporalStatus.EVENT_BOUND: "open",
    TemporalStatus.OPEN: "open",
    TemporalStatus.UNSUPPORTED: "unsupported",
}


def _span_ref(unit_source_unit_id: str, unit_source_hash: str, unit_work_id: str,
              unit_address: str | None, raw_text: str, expr: TemporalExpr) -> SourceSpanRef:
    """Build a raw_text-relative SourceSpanRef from the recognizer's own offsets."""
    start = expr.source_span.byte_offset
    end = start + expr.source_span.byte_len
    surface = raw_text[start:end]
    return SourceSpanRef(
        source_unit_id=unit_source_unit_id,
        source_hash=unit_source_hash,
        work_id=unit_work_id,
        address=unit_address,
        char_start=start,
        char_end=end,
        text_hash=hashlib.sha256(surface.encode("utf-8")).hexdigest(),
    )


class TemporalLens:
    """SurfaceLens adapter over the H3 temporal recognizer (nodes only)."""

    lens_id: str = _LENS_ID
    jurisdiction: str = "fi"
    schema_version: str = "v0"
    produces_node_kinds: tuple[str, ...] = ("temporal_expr",)
    produces_edge_kinds: tuple[str, ...] = ()
    required_views: tuple[str, ...] = ("raw_text",)

    def analyze(
        self,
        bundle: SourceSurfaceBundle,
        *,
        context: SurfaceAnalysisContext,
    ) -> SurfaceLensResult:
        node_seeds: list[SurfaceNodeSeed] = []
        units_scanned = 0
        for unit in bundle.units:
            units_scanned += 1
            exprs = recognize_temporal_exprs(unit.raw_text)
            for expr in exprs:
                ref = _span_ref(
                    unit.source_unit_id,
                    unit.source_hash,
                    unit.work_id,
                    unit.address,
                    unit.raw_text,
                    expr,
                )
                temporal_kind = expr.kind.value
                date_value = expr.bound.isoformat() if expr.bound is not None else None
                status = _STATUS_MAP[expr.status]
                node_seeds.append(
                    SurfaceNodeSeed(
                        node_kind="temporal_expr",
                        source_ref=ref,
                        # discriminator distinguishes co-located cues of different
                        # kinds/surfaces sharing a span window.
                        local_discriminator=f"{temporal_kind}|{expr.surface_text}",
                        rule_id=expr.rule_id,
                        status=status,
                        payload={
                            "temporal_kind": temporal_kind,
                            "surface_text": expr.surface_text,
                            "date_value": date_value,
                            "status": status,
                        },
                        authority_role="surface_fact",
                    )
                )

        return SurfaceLensResult(
            lens_id=self.lens_id,
            node_seeds=tuple(node_seeds),
            edge_seeds=(),
            residuals=(),
            diagnostics=(),
            coverage={"units_scanned": units_scanned, "temporal_exprs": len(node_seeds)},
        )
