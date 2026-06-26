"""H4 actor/modal surface lens (Pro r5 Phase 6 — nodes only).

Adapts :func:`lawvm.finland.references.actor_modal.recognize_actor_modal_frames`
into a :class:`lawvm.core.legal_surface_lens.SurfaceLens`. It emits one
``actor_modal_frame`` surface node per recognised :class:`ActorModalFrame`, a
``surface_residual`` seed per :class:`ActorModalResidual` (fail-loud — a shape the
recognizer saw but could not type is never dropped), and NO edges. Cross-lens
actor↔temporal attachment is explicitly DEFERRED per Pro r5 Phase 6 ("no cross-lens
semantics yet"); the actor/object linkage stays a within-payload surface span, not
a graph edge.

SAFETY BOUNDARY (mirrors the recognizer): SURFACE FACTS ONLY. A node records the
*form* of the actor+modal shape (``modal_token``, ``polarity``, ``voice``), never a
legal conclusion (no "duty"/"power"/"obligation"). Status is the structural
``NODE_STATUSES`` value ``"asserted"`` — the frame is an asserted surface fact, not
a resolution outcome (the recognizer's own status is the constant
``"surface_fact_only"``, which is not a graph resolution status; "asserted" is the
correct closed-vocabulary mapping for "this surface fact is present and owned").

Span alignment (Pro r5 §"span alignment"): the recognizer is now TOKEN-NATIVE —
it consumes ``unit.token_tape`` (a :class:`TokenTape` over ``unit.raw_text``) and
reports WHOLE-TOKEN-aligned character offsets into that same text. We build each
``SourceSpanRef`` DIRECTLY from the recognizer's offsets rather than re-locating
via ``locate_span``. Spans are token-aligned (re-baselined vs. the prior
char-regex spans); the lens stays a thin adapter. The whole-frame span (actor
start .. object/modal end) anchors the node; the object span travels in the
payload.
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
from lawvm.core.legal_surface_tokens import TokenTape
from lawvm.core.reference_mention import SourceSpan
from lawvm.finland.references.actor_modal import (
    ActorModalFrame,
    ActorModalResidual,
    recognize_actor_modal_frames,
)

_LENS_ID = "fi.actor_modal.v0"


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


def _object_span_payload(frame: ActorModalFrame) -> list[int] | None:
    """[char_start, char_end] of the object surface span, or None."""
    obj = frame.object_span
    if obj is None:
        return None
    return [obj.byte_offset, obj.byte_offset + obj.byte_len]


class ActorModalLens:
    """SurfaceLens adapter over the H4 actor/modal recognizer (nodes only)."""

    lens_id: str = _LENS_ID
    jurisdiction: str = "fi"
    schema_version: str = "v0"
    produces_node_kinds: tuple[str, ...] = ("actor_modal_frame",)
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
            tape = unit.token_tape
            if not isinstance(tape, TokenTape):
                raise ValueError(
                    "ActorModalLens requires a populated token_tape view "
                    f"(required_views={self.required_views!r}); unit "
                    f"{unit.source_unit_id!r} has token_tape={type(tape).__name__}"
                )
            scan = recognize_actor_modal_frames(tape)
            for frame in scan.frames:
                ref = _span_ref(
                    unit.source_unit_id,
                    unit.source_hash,
                    unit.work_id,
                    unit.address,
                    unit.raw_text,
                    frame.source_span,
                )
                object_span = _object_span_payload(frame)
                node_seeds.append(
                    SurfaceNodeSeed(
                        node_kind="actor_modal_frame",
                        source_ref=ref,
                        local_discriminator=(
                            f"{frame.actor_surface}|{frame.modal.token}|"
                            f"{frame.source_span.byte_offset}"
                        ),
                        rule_id=frame.rule_id,
                        # NODE_STATUSES structural value: an owned, present surface
                        # fact (NOT a resolution outcome; the recognizer's own
                        # "surface_fact_only" is not a graph resolution status).
                        node_status="asserted",
                        payload={
                            "actor_surface": frame.actor_surface,
                            "modal_token": frame.modal.token,
                            "polarity": frame.modal.polarity,
                            "voice": frame.modal.voice,
                            "object_span": object_span,
                        },
                        authority_role="surface_fact",
                    )
                )
            for residual in scan.residuals:
                residual_seeds.append(_residual_seed(unit, residual))

        return SurfaceLensResult(
            lens_id=self.lens_id,
            node_seeds=tuple(node_seeds),
            edge_seeds=(),
            residuals=tuple(residual_seeds),
            diagnostics=(),
            coverage={
                "units_scanned": units_scanned,
                "actor_modal_frames": len(node_seeds),
                "residuals": len(residual_seeds),
            },
        )


def _residual_seed(
    unit: SourceSurfaceUnit, residual: ActorModalResidual
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
        residual_kind=f"actor_modal.{residual.kind}",
        source_ref=ref,
        local_discriminator=(
            f"{residual.kind}|{residual.surface_text}|{residual.source_span.byte_offset}"
        ),
        rule_id=_LENS_ID,
        reason_code=residual.kind,
        payload={
            "surface_text": residual.surface_text,
            "detail": residual.detail,
        },
        residual_status="open",
    )
