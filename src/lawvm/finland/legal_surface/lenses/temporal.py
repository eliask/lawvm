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
    SourceSurfaceUnit,
    SurfaceAnalysisContext,
    SurfaceLensResult,
    SurfaceNodeSeed,
)
from lawvm.finland.references.temporal import (
    TemporalExpr,
    TemporalKind,
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
              unit_address: str | None, raw_text: str, expr: TemporalExpr,
              base: int = 0) -> SourceSpanRef:
    """Build a raw_text-relative SourceSpanRef from the recognizer's own offsets.

    ``base`` is the raw_text offset of the text the recognizer ran on: ``0`` when
    it scanned the whole unit (the golden-reference scan), or the gated segment's
    ``char_start`` when the recognizer was re-run on a forest-gated segment (the
    forest projection). In both cases the resulting span is raw_text-absolute, so a
    forest-projected node anchors to the SAME raw_text span as a whole-unit-scanned
    one — byte-identical by construction.
    """
    start = base + expr.source_span.byte_offset
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


#: The SHARED-CANONICAL node kinds the forest produces (the slice this flip routes
#: through the forest). At the NODE level the only temporal kind that is BOTH a
#: shared canonical core (commencement / expiry, dated) AND not also a lens-only
#: residual kind is :attr:`TemporalKind.FIXED_TERM_EXPIRY`: it carries its OWN
#: ISO date on a single node. A dated commencement, by contrast, is split by the
#: recognizer into a DATELESS ``COMMENCEMENT`` cue node plus a ``FIXED_DATE`` node
#: carrying the date — and ``FIXED_DATE`` is a lens-only residual kind (a bare date
#: with no operator is the same node kind), so the commencement DATE cannot leave
#: the residual scan without splitting a residual kind. The flippable shared NODE
#: slice is therefore exactly the fixed-term-expiry node.
FOREST_SHARED_TEMPORAL_KINDS: frozenset[TemporalKind] = frozenset(
    {TemporalKind.FIXED_TERM_EXPIRY}
)


def mint_temporal_expr_seed(
    unit: SourceSurfaceUnit, expr: TemporalExpr, base: int = 0
) -> SurfaceNodeSeed:
    """Mint ONE ``temporal_expr`` node seed for a recognised expr in raw_text coords.

    THE shared seed-minting authority: both the production whole-unit scan
    (:func:`temporal_seeds_for_unit`, the golden reference) and the forest-gated
    shared-slice projection
    (:func:`…temporal_projection.project_forest_temporal_seeds`) mint via this one
    function, so a forest-projected node is byte-identical to a scanned node BY
    CONSTRUCTION (same raw_text-absolute span, discriminator, rule_id, payload).
    ``base`` is the raw_text offset of the text the recognizer ran on (``0`` for a
    whole-unit scan, the gated segment's ``char_start`` for the projection).
    """
    ref = _span_ref(
        unit.source_unit_id,
        unit.source_hash,
        unit.work_id,
        unit.address,
        unit.raw_text,
        expr,
        base=base,
    )
    temporal_kind = expr.kind.value
    date_value = expr.bound.isoformat() if expr.bound is not None else None
    node_status = _STATUS_MAP[expr.status]
    return SurfaceNodeSeed(
        node_kind="temporal_expr",
        source_ref=ref,
        # discriminator distinguishes co-located cues of different kinds/surfaces
        # sharing a span window.
        local_discriminator=f"{temporal_kind}|{expr.surface_text}",
        rule_id=expr.rule_id,
        node_status=node_status,
        payload={
            "temporal_kind": temporal_kind,
            "surface_text": expr.surface_text,
            "date_value": date_value,
            "node_status": node_status,
        },
        authority_role="surface_fact",
    )


def temporal_seeds_for_unit(unit: SourceSurfaceUnit) -> list[SurfaceNodeSeed]:
    """The INDEPENDENT (golden-reference) whole-unit ``temporal_expr`` seed scan.

    Runs :func:`recognize_temporal_exprs` over the whole ``unit.raw_text`` and
    mints one seed per expr through :func:`mint_temporal_expr_seed`. This is the
    GOLDEN REFERENCE the production flip is differenced against: production now
    routes the SHARED-CANONICAL slice
    (:data:`FOREST_SHARED_TEMPORAL_KINDS`) through the cached forest
    (:func:`…temporal_projection.project_forest_temporal_seeds`) and emits the
    remaining (lens-only residual) kinds from this same scan — proven node-identical
    to this whole-unit scan corpus-wide.
    """
    return [
        mint_temporal_expr_seed(unit, expr)
        for expr in recognize_temporal_exprs(unit.raw_text)
    ]


class TemporalLens:
    """SurfaceLens adapter over the H3 temporal recognizer (nodes only).

    PARTIAL PRODUCTION STRANGLE-FLIP (doc-6): the SHARED-CANONICAL temporal node
    slice (:data:`FOREST_SHARED_TEMPORAL_KINDS` — the dated fixed-term-expiry node)
    now comes FROM the cached :class:`SourceSyntaxGraph` forest projection
    (:func:`…temporal_projection.project_forest_temporal_seeds`) rather than the
    whole-unit recognizer scan. The forest is the PRODUCER for that slice: its
    temporal-family-owned spans gate which structural segments carry the cue, and
    the segment is reconstructed via the SAME recognizer, minting through the SAME
    :func:`mint_temporal_expr_seed` authority — so the flipped slice is byte-
    identical to the prior scan (proven 0-delta corpus-wide).

    The LENS-ONLY RESIDUAL kinds (bare ``FIXED_DATE`` with no operator cue, the
    dateless ``COMMENCEMENT`` cue + its companion ``FIXED_DATE`` date,
    ``DURATION_FROM_COMMENCEMENT``, ``EVENT_BOUND``, undated ``VALIDITY_OPEN``) are
    NOT flippable at the node level (the commencement DATE is a ``FIXED_DATE``
    residual node) and CONTINUE to be emitted from the whole-unit scan
    (:func:`temporal_seeds_for_unit`, also the golden reference). The TOTAL node set
    is therefore unchanged (0-delta); only the shared-canonical slice flows FROM the
    forest.
    """

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
        # Import here to avoid a module import cycle (temporal_projection imports
        # the seed/bundle types this lens also uses + the source_syntax_graph it
        # reads). The projection reads the CACHED forest, so production mints the
        # SHARED-CANONICAL temporal slice FROM the forest (the flip).
        from lawvm.finland.legal_surface.temporal_projection import (
            project_forest_temporal_seeds,
        )

        node_seeds: list[SurfaceNodeSeed] = []
        # (1) SHARED-CANONICAL slice (fixed-term expiry) — FROM the forest.
        node_seeds.extend(project_forest_temporal_seeds(bundle))
        # (2) lens-only RESIDUAL kinds — from the whole-unit scan (golden ref),
        #     filtered to the kinds NOT routed through the forest.
        for unit in bundle.units:
            for expr in recognize_temporal_exprs(unit.raw_text):
                if expr.kind in FOREST_SHARED_TEMPORAL_KINDS:
                    continue
                node_seeds.append(mint_temporal_expr_seed(unit, expr))

        return SurfaceLensResult(
            lens_id=self.lens_id,
            node_seeds=tuple(node_seeds),
            edge_seeds=(),
            residuals=(),
            diagnostics=(),
            coverage={
                "units_scanned": len(bundle.units),
                "temporal_exprs": len(node_seeds),
            },
        )
