"""H5 sanction/consequence surface lens (Pro r5 Phase 8 — nodes only).

Adapts :func:`lawvm.finland.references.sanction.recognize_sanction_frames` into
a :class:`lawvm.core.legal_surface_lens.SurfaceLens`. It emits one
``sanction_frame`` surface node per recognised :class:`SanctionFrame`, a
``surface_residual`` seed per :class:`SanctionResidual` (fail-loud — a
sanction-shaped token the recognizer saw but could not type, or a revocation
without a permit noun, is never dropped), and NO edges. Cross-frame edges/lints
(sanction ↔ target actor ↔ trigger) are DEFERRED to a later phase per Pro r5
Phase 8.

SAFETY BOUNDARY (mirrors the recognizer): SURFACE FACTS ONLY. A node records the
*form* of the sanction marker (``sanction_kind``, ``marker_surface``), never a
legal conclusion (no "guilt", "liability", "culpability", "enforceable").
Status is the structural ``NODE_STATUSES`` value ``"asserted"`` (the same choice
ActorModalLens made; the recognizer's own "surface_fact_only" is not a graph
resolution status).

Span alignment (Pro r5 §"span alignment"): the recognizer reports CHARACTER
offsets relative to the text it was given. We feed it ``unit.raw_text`` verbatim
and build each ``SourceSpanRef`` DIRECTLY from those offsets. The whole-frame
span anchors the node; the target-actor/trigger spans travel in the payload.
"""
from __future__ import annotations

import hashlib

from lawvm.core.legal_surface_graph import SourceSpanRef
from lawvm.core.legal_surface_lens import (
    SourceSurfaceBundle,
    SourceSurfaceUnit,
    SurfaceAnalysisContext,
    SurfaceLensResult,
    SurfaceNodeSeed,
    SurfaceResidualSeed,
)
from lawvm.core.reference_mention import SourceSpan
from lawvm.finland.references.sanction import (
    SanctionFrame,
    SanctionResidual,
    recognize_sanction_frames,
)

_LENS_ID = "fi.sanction.v0"


def _span_ref(unit_source_unit_id: str, unit_source_hash: str, unit_work_id: str,
              unit_address: str | None, raw_text: str, span: SourceSpan) -> SourceSpanRef:
    """Build a raw_text-relative SourceSpanRef from a recognizer SourceSpan."""
    start = span.byte_offset
    end = start + span.byte_len
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


def _opt_span_payload(span: SourceSpan | None) -> list[int] | None:
    """[char_start, char_end] of an optional recognizer span, or None."""
    if span is None:
        return None
    return [span.byte_offset, span.byte_offset + span.byte_len]


class SanctionLens:
    """SurfaceLens adapter over the H5 sanction recognizer (nodes only)."""

    lens_id: str = _LENS_ID
    jurisdiction: str = "fi"
    schema_version: str = "v0"
    produces_node_kinds: tuple[str, ...] = ("sanction_frame",)
    produces_edge_kinds: tuple[str, ...] = ()
    required_views: tuple[str, ...] = ("raw_text",)

    def analyze(
        self,
        bundle: SourceSurfaceBundle,
        *,
        context: SurfaceAnalysisContext,
    ) -> SurfaceLensResult:
        node_seeds: list[SurfaceNodeSeed] = []
        residual_seeds: list[SurfaceResidualSeed] = []
        units_scanned = 0
        for unit in bundle.units:
            units_scanned += 1
            scan = recognize_sanction_frames(unit.raw_text)
            for frame in scan.frames:
                # A monotonic index keeps the discriminator unique when two
                # frames share kind+marker+offset but differ in payload
                # (co-located sanction markers) — neither is dropped.
                node_seeds.append(
                    self._node_seed(unit, frame, len(node_seeds))
                )
            for residual in scan.residuals:
                residual_seeds.append(
                    _residual_seed(unit, residual, len(residual_seeds))
                )

        return SurfaceLensResult(
            lens_id=self.lens_id,
            node_seeds=tuple(node_seeds),
            edge_seeds=(),
            residuals=tuple(residual_seeds),
            diagnostics=(),
            coverage={
                "units_scanned": units_scanned,
                "sanction_frames": len(node_seeds),
                "residuals": len(residual_seeds),
            },
        )

    def _node_seed(
        self, unit: SourceSurfaceUnit, frame: SanctionFrame, index: int
    ) -> SurfaceNodeSeed:
        ref = _span_ref(
            unit.source_unit_id,
            unit.source_hash,
            unit.work_id,
            unit.address,
            unit.raw_text,
            frame.source_span,
        )
        sanction_kind = frame.sanction_kind.value
        return SurfaceNodeSeed(
            node_kind="sanction_frame",
            source_ref=ref,
            local_discriminator=(
                f"{sanction_kind}|{frame.marker_surface}|"
                f"{frame.source_span.byte_offset}|{index}"
            ),
            rule_id=frame.rule_id,
            # NODE_STATUSES structural value: an owned, present surface fact
            # (NOT a resolution outcome).
            status="asserted",
            payload={
                "sanction_kind": sanction_kind,
                "marker_surface": frame.marker_surface,
                "target_actor_span": _opt_span_payload(frame.target_actor_span),
                "trigger_span": _opt_span_payload(frame.trigger_span),
            },
            authority_role="surface_fact",
        )


def _residual_seed(
    unit: SourceSurfaceUnit, residual: SanctionResidual, index: int
) -> SurfaceResidualSeed:
    ref = _span_ref(
        unit.source_unit_id,
        unit.source_hash,
        unit.work_id,
        unit.address,
        unit.raw_text,
        residual.source_span,
    )
    return SurfaceResidualSeed(
        residual_kind=f"sanction.{residual.kind}",
        source_ref=ref,
        local_discriminator=(
            f"{residual.kind}|{residual.surface_text}|"
            f"{residual.source_span.byte_offset}|{index}"
        ),
        rule_id=_LENS_ID,
        reason_code=residual.kind,
        payload={
            "surface_text": residual.surface_text,
            "detail": residual.detail,
        },
        status="open",
    )
