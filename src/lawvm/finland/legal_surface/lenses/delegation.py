"""H5 delegation/authority surface lens (Pro r5 Phase 8 — nodes only).

Adapts :func:`lawvm.finland.references.delegation.recognize_delegation_frames`
into a :class:`lawvm.core.legal_surface_lens.SurfaceLens`. It emits one
``delegation_frame`` surface node per recognised :class:`DelegationFrame`, a
``surface_residual`` seed per :class:`DelegationResidual` (fail-loud — a
delegation-shaped clause the recognizer saw but could not type is never
dropped), and NO edges. Cross-frame edges/lints (delegation ↔ instrument ↔
actor) are explicitly DEFERRED to a later phase per Pro r5 ("add the frame
families as graph node kinds; defer frame-specific edges/lints").

SAFETY BOUNDARY (mirrors the recognizer): SURFACE FACTS ONLY. A node records the
*form* of the delegation shape (``delegate_actor``, ``instrument_kind``,
``binding_strength``), never a legal conclusion (no "valid delegation", no
"power", no "discretion", no "ultra vires"). Status is the structural
``NODE_STATUSES`` value ``"asserted"`` — the frame is an asserted surface fact,
not a resolution outcome (the recognizer's own "surface_fact_only" is not a
graph resolution status; "asserted" is the closed-vocabulary mapping for "this
surface fact is present and owned", the same choice ActorModalLens made).

Span alignment (Pro r5 §"span alignment"): the recognizer is now TOKEN-NATIVE —
it consumes ``unit.token_tape`` (a :class:`TokenTape` over ``unit.raw_text``) and
reports WHOLE-TOKEN-aligned character offsets into that same text. We build each
``SourceSpanRef`` DIRECTLY from the recognizer's offsets rather than re-locating
via ``locate_span``. Spans are token-aligned (re-baselined vs. the prior
char-regex spans); the lens stays a thin adapter. The whole-frame span anchors
the node; the subject span travels in the payload.
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
from lawvm.finland.references.delegation import (
    DelegationFrame,
    DelegationResidual,
    recognize_delegation_frames,
)

_LENS_ID = "fi.delegation.v0"


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


def _subject_span_payload(frame: DelegationFrame) -> list[int] | None:
    """[char_start, char_end] of the trailing subject surface span, or None."""
    subj = frame.subject_span
    if subj is None:
        return None
    return [subj.byte_offset, subj.byte_offset + subj.byte_len]


class DelegationLens:
    """SurfaceLens adapter over the H5 delegation recognizer (nodes only)."""

    lens_id: str = _LENS_ID
    jurisdiction: str = "fi"
    schema_version: str = "v0"
    produces_node_kinds: tuple[str, ...] = ("delegation_frame",)
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
                    "DelegationLens requires a populated token_tape view "
                    f"(required_views={self.required_views!r}); unit "
                    f"{unit.source_unit_id!r} has token_tape={type(tape).__name__}"
                )
            scan = recognize_delegation_frames(tape)
            for frame in scan.frames:
                ref = _span_ref(
                    unit.source_unit_id,
                    unit.source_hash,
                    unit.work_id,
                    unit.address,
                    unit.raw_text,
                    frame.source_span,
                )
                node_seeds.append(
                    SurfaceNodeSeed(
                        node_kind="delegation_frame",
                        source_ref=ref,
                        local_discriminator=(
                            f"{frame.delegate_actor}|{frame.instrument_kind}|"
                            f"{frame.source_span.byte_offset}|{len(node_seeds)}"
                        ),
                        rule_id=frame.rule_id,
                        # NODE_STATUSES structural value: an owned, present surface
                        # fact (NOT a resolution outcome).
                        node_status="asserted",
                        payload={
                            "delegate_actor": frame.delegate_actor,
                            "instrument_kind": frame.instrument_kind,
                            "binding_strength": frame.binding_strength,
                            "subject_span": _subject_span_payload(frame),
                        },
                        authority_role="surface_fact",
                    )
                )
            for residual in scan.residuals:
                # A monotonic index keeps the discriminator unique when two
                # residuals share kind+surface+offset but differ in payload
                # (co-located actorless clauses) — neither is dropped.
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
                "delegation_frames": len(node_seeds),
                "residuals": len(residual_seeds),
            },
        )


def _residual_seed(
    unit: SourceSurfaceUnit, residual: DelegationResidual, index: int
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
        residual_kind=f"delegation.{residual.kind}",
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
        residual_status="open",
    )
