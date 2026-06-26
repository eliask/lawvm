"""H5 procedure surface lens (Pro r5 Phase 8 — nodes only).

Adapts the H5 procedure recognizer
(:mod:`lawvm.finland.references.procedure`) into a
:class:`lawvm.core.legal_surface_lens.SurfaceLens`. It emits one
``procedure_frame`` surface node per recognised :class:`ProcedureFrame`, a
``surface_residual`` seed per :class:`ProcedureResidual` (fail-loud — a
process-shaped token the recognizer saw but could not type to the closed kind
set is never dropped), and NO edges. Cross-frame edges/lints are DEFERRED to a
later phase per Pro r5 Phase 8.

We call :func:`scan_procedure` rather than the frames-only
:func:`recognize_procedure_frames` convenience wrapper precisely so the typed
residuals are surfaced (the wrapper discards them; dropping them would violate
the fail-loud boundary). The recognizer entry point stays explicit.

SAFETY BOUNDARY (mirrors the recognizer): SURFACE FACTS ONLY. A node records the
*form* of the process noun (``process_kind``), never a legal conclusion (no
"valid", "made", "enforceable"). Status is the structural ``NODE_STATUSES``
value ``"asserted"`` (the same choice ActorModalLens made; the recognizer's own
"surface_fact_only" is not a graph resolution status).

Span alignment (Pro r5 §"span alignment"): the recognizer is a TOKEN/GRAMMAR
recognizer over the source-preserving ``token_tape`` view (Phase 7); its spans
are TOKEN-ALIGNED character offsets into ``unit.raw_text``. We feed it the
unit's tape (+ morph overlay) and build each ``SourceSpanRef`` DIRECTLY from
those offsets. ``required_views=("token_tape",)``. The process-noun head span
anchors the node; the actor/deadline spans travel in the payload.
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
from lawvm.core.legal_surface_tokens import MorphOverlay, TokenTape
from lawvm.core.reference_mention import SourceSpan
from lawvm.finland.legal_surface.tokenize import (
    build_morph_overlay,
    build_token_tape,
)
from lawvm.finland.references.procedure import (
    ProcedureResidual,
    scan_procedure,
)

_LENS_ID = "fi.procedure.v0"


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


class ProcedureLens:
    """SurfaceLens adapter over the H5 procedure recognizer (nodes only)."""

    lens_id: str = _LENS_ID
    jurisdiction: str = "fi"
    schema_version: str = "v0"
    produces_node_kinds: tuple[str, ...] = ("procedure_frame", "procedure_cue")
    produces_edge_kinds: tuple[str, ...] = ()
    required_views: tuple[str, ...] = ("token_tape",)

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
            # The substrate populates token_tape/morph_overlay; tolerate an
            # un-tokenized unit by building on demand (fail-loud only if
            # raw_text is absent). The tape's spans index into unit.raw_text.
            tape = unit.token_tape
            if not isinstance(tape, TokenTape):
                tape = build_token_tape(unit.source_unit_id, unit.raw_text)
            overlay = unit.morph_overlay
            if not isinstance(overlay, MorphOverlay):
                overlay = build_morph_overlay(tape)
            scan = scan_procedure(unit.raw_text, tape=tape, overlay=overlay)
            for frame in scan.frames:
                ref = _span_ref(
                    unit.source_unit_id,
                    unit.source_hash,
                    unit.work_id,
                    unit.address,
                    unit.raw_text,
                    frame.source_span,
                )
                process_kind = frame.process_kind.value
                actor_payload = _opt_span_payload(frame.actor_span)
                deadline_payload = _opt_span_payload(frame.deadline_span)
                # A frame with NONE of its defining flanks (no actor AND no
                # deadline) carries no frame structure — it is a bare process-noun
                # CUE. Demote it to ``procedure_cue`` (no-fabrication: the "frame"
                # name would over-claim), keeping the span + process_kind
                # (totality). A frame WITH content stays ``procedure_frame``.
                admissible_as_frame = (
                    actor_payload is not None or deadline_payload is not None
                )
                node_kind = (
                    "procedure_frame" if admissible_as_frame else "procedure_cue"
                )
                node_seeds.append(
                    SurfaceNodeSeed(
                        node_kind=node_kind,
                        source_ref=ref,
                        local_discriminator=(
                            f"{process_kind}|{frame.source_span.byte_offset}|"
                            f"{len(node_seeds)}"
                        ),
                        rule_id=frame.rule_id,
                        # NODE_STATUSES structural value: an owned, present surface
                        # fact (NOT a resolution outcome).
                        node_status="asserted",
                        payload={
                            "process_kind": process_kind,
                            "actor_span": actor_payload,
                            "deadline_span": deadline_payload,
                            "admissible_as_frame": admissible_as_frame,
                        },
                        authority_role="surface_fact",
                    )
                )
            for residual in scan.residuals:
                # A monotonic index keeps the discriminator unique when two
                # residuals share surface+offset but differ in payload
                # (co-located process tokens) — neither is dropped.
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
                "procedure_frames": sum(
                    1 for s in node_seeds if s.node_kind == "procedure_frame"
                ),
                "procedure_cues": sum(
                    1 for s in node_seeds if s.node_kind == "procedure_cue"
                ),
                "residuals": len(residual_seeds),
            },
        )


def _residual_seed(
    unit: SourceSurfaceUnit, residual: ProcedureResidual, index: int
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
        residual_kind="procedure.untypeable_process_token",
        source_ref=ref,
        local_discriminator=(
            f"{residual.surface_text}|{residual.source_span.byte_offset}|"
            f"{index}"
        ),
        rule_id=_LENS_ID,
        reason_code="untypeable_process_token",
        payload={
            "surface_text": residual.surface_text,
            "detail": residual.detail,
        },
        residual_status="open",
    )
