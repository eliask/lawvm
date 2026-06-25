"""Anaphora surface lens — adapter from the discourse anaphora recognizer.

Adapts :func:`lawvm.finland.references.anaphora.recognize_anaphoric_refs` into a
:class:`lawvm.core.legal_surface_lens.SurfaceLens`. Discourse-level anaphoric
references (``mainitun lain``, ``sanotun pykälän``, ``tämän lain``) become graph
nodes like any other reference: a ``reference_expr`` node (what the text SAYS),
a ``reference_resolution`` node (what it POINTS BACK to), and a ``resolution_of``
edge (resolution -> expr). It reuses the H1 reference node/edge KINDS — a
DISTINCT ``lens_id`` ("fi.anaphora.v0") keeps the minted node ids disjoint from
the ReferenceLens's nodes (``mint_source_fact_node_id`` folds ``lens_id`` into
the identity tuple), so downstream censuses (and ``lawvm surface-graph``) count
the anaphora references uniformly alongside the H1 ones without collision.

PURE ADAPTER. The recognizer owns resolution; it resolves an anaphor against
its in-text ANTECEDENT (the nearest preceding concrete reference of the matching
kind), NOT against the statute registry. This lens does NOT re-resolve, does NOT
pick among candidates, and does NOT consult any registry — it only transcribes
the recognizer's verdict onto the graph.

SAFETY BOUNDARY (mirrors the recognizer): SURFACE FACTS ONLY. A node records the
*form* of the anaphor (its surface, its head kind, its resolution status, the
bound antecedent target when RESOLVED), never a legal conclusion. The resolution
status vocabulary mirrors the H1 ReferenceLens — ``resolved`` / ``ambiguous`` /
``open`` — so a uniform census reads both lenses the same way. An anaphor that
resolves to nothing (OPEN) still emits BOTH the expr and an ``open`` resolution
node — never a silent drop.

Span alignment (the DelegationLens pattern): ``recognize_anaphoric_refs`` reports
a ``char_offset`` (a true 0-based CHAR offset into the text we fed in). We feed
it ``unit.raw_text`` verbatim, so we build each ``SourceSpanRef`` DIRECTLY from
``char_offset`` + ``len(surface_text)`` rather than re-locating via
``locate_span`` — exact, and correct for repeated surfaces.
"""
from __future__ import annotations

import hashlib

from lawvm.core.legal_surface_graph import SourceSpanRef
from lawvm.core.legal_surface_lens import (
    SourceSurfaceBundle,
    SourceSurfaceUnit,
    SurfaceAnalysisContext,
    SurfaceEdgeSeed,
    SurfaceLensResult,
    SurfaceNodeSeed,
    SurfaceResidualSeed,
)
from lawvm.finland.references.anaphora import (
    AnaphoricRef,
    AnaphorStatus,
    recognize_anaphoric_refs,
)

LENS_ID = "fi.anaphora.v0"
SCHEMA_VERSION = "v0"

# Rule ids (stable; the witness/identity carrier for each emitted seed family).
_RULE_EXPR = "fi.anaphora.v0.reference_expr"
_RULE_RESOLUTION = "fi.anaphora.v0.reference_resolution"
_RULE_RESOLUTION_OF = "fi.anaphora.v0.resolution_of"

# AnaphorStatus -> graph node status (the H1 ReferenceLens resolution vocabulary,
# so a uniform census counts anaphora references identically). RESOLVED -> the
# antecedent target is fixed; AMBIGUOUS -> several equally-recent candidates, none
# picked; OPEN -> no antecedent in scope, no target by design.
_STATUS_TO_GRAPH: dict[AnaphorStatus, str] = {
    AnaphorStatus.RESOLVED: "resolved",
    AnaphorStatus.AMBIGUOUS: "ambiguous",
    AnaphorStatus.OPEN: "open",
}


def _graph_status(anaphor_status: AnaphorStatus) -> str:
    """Map an AnaphorStatus to a graph node status (fail-loud on the unmapped)."""
    try:
        return _STATUS_TO_GRAPH[anaphor_status]
    except KeyError as exc:  # pragma: no cover — closed enum, defensive
        raise ValueError(
            f"{LENS_ID}: no graph status mapping for AnaphorStatus {anaphor_status!r}"
        ) from exc


def _expr_local(index: int) -> str:
    return f"{LENS_ID}::expr#{index}"


def _resolution_local(index: int) -> str:
    return f"{LENS_ID}::resolution#{index}"


def _span_ref(unit: SourceSurfaceUnit, ref: AnaphoricRef) -> SourceSpanRef:
    """Build a raw_text-relative SourceSpanRef from the recognizer's char offset.

    ``recognize_anaphoric_refs`` ran over ``unit.raw_text`` verbatim, so its
    ``char_offset`` is a CHAR coordinate into that very text. We span it directly
    (offset .. offset+len(surface)) — the DelegationLens direct-offset pattern,
    correct for repeated surfaces (no re-location ambiguity).
    """
    start = ref.char_offset
    end = start + len(ref.surface_text)
    surface = unit.raw_text[start:end]
    return SourceSpanRef(
        source_unit_id=unit.source_unit_id,
        source_hash=unit.source_hash,
        work_id=unit.work_id,
        address=unit.address,
        char_start=start,
        char_end=end,
        text_hash=hashlib.sha256(surface.encode("utf-8")).hexdigest(),
    )


def _target_provision_ref(ref: AnaphoricRef) -> str | None:
    """The bound antecedent target (serialized), or None for AMBIGUOUS / OPEN.

    RESOLVED carries the bound target on its embedded mention; AMBIGUOUS / OPEN
    carry ``target_provision_ref=None`` (the recognizer picks nothing).
    """
    target = ref.mention.target_provision_ref
    return target.serialized() if target is not None else None


def _candidate_ids(ref: AnaphoricRef) -> list[str]:
    """The AMBIGUOUS candidate targets (serialized); empty otherwise. Never picks."""
    return [c.serialized() for c in ref.candidates]


class AnaphoraLens:
    """Discourse anaphora surface lens (satisfies the ``SurfaceLens`` protocol)."""

    lens_id: str = LENS_ID
    jurisdiction: str = "fi"
    schema_version: str = SCHEMA_VERSION
    produces_node_kinds: tuple[str, ...] = (
        "reference_expr",
        "reference_resolution",
    )
    produces_edge_kinds: tuple[str, ...] = ("resolution_of",)
    required_views: tuple[str, ...] = ("raw_text",)

    def analyze(
        self,
        bundle: SourceSurfaceBundle,
        *,
        context: SurfaceAnalysisContext,
    ) -> SurfaceLensResult:
        node_seeds: list[SurfaceNodeSeed] = []
        edge_seeds: list[SurfaceEdgeSeed] = []
        residuals: list[SurfaceResidualSeed] = []

        n_units = 0
        n_anaphors = 0
        n_resolved = 0
        n_ambiguous = 0
        n_open = 0

        # Global running index so reference_expr / reference_resolution
        # discriminators are unique across ALL units in the bundle (deterministic
        # — units iterate in bundle order, anaphors in document order).
        index = 0

        for unit in bundle.units:
            n_units += 1
            for ref in recognize_anaphoric_refs(unit.raw_text, unit.work_id):
                n_anaphors += 1
                status = ref.status
                if status is AnaphorStatus.RESOLVED:
                    n_resolved += 1
                elif status is AnaphorStatus.AMBIGUOUS:
                    n_ambiguous += 1
                else:
                    n_open += 1

                node_status = _graph_status(status)
                source_ref = _span_ref(unit, ref)
                expr_local = _expr_local(index)
                resolution_local = _resolution_local(index)

                node_seeds.append(
                    self._reference_expr_seed(
                        ref, source_ref=source_ref, resolution_status=node_status, local=expr_local
                    )
                )
                node_seeds.append(
                    self._reference_resolution_seed(
                        ref,
                        source_ref=source_ref,
                        resolution_status=node_status,
                        local=resolution_local,
                    )
                )
                edge_seeds.append(
                    SurfaceEdgeSeed(
                        edge_kind="resolution_of",
                        src_local=resolution_local,
                        dst_local=expr_local,
                        rule_id=_RULE_RESOLUTION_OF,
                        status="asserted",
                        payload={},
                    )
                )

                index += 1

        coverage: dict[str, object] = {
            "units": n_units,
            "anaphors": n_anaphors,
            "resolved": n_resolved,
            "ambiguous": n_ambiguous,
            "open": n_open,
        }

        return SurfaceLensResult(
            lens_id=LENS_ID,
            node_seeds=tuple(node_seeds),
            edge_seeds=tuple(edge_seeds),
            residuals=tuple(residuals),
            diagnostics=(),
            coverage=coverage,
        )

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _reference_expr_seed(
        ref: AnaphoricRef,
        *,
        source_ref: SourceSpanRef,
        resolution_status: str,
        local: str,
    ) -> SurfaceNodeSeed:
        """The expr node: what the anaphor SAYS (its surface + head kind)."""
        payload: dict[str, object] = {
            "anaphor_surface": ref.surface_text,
            "head_kind": ref.head_kind.value,
            "cite_kind": ref.mention.cite_kind.value,
            "phrase_lemma": ref.mention.phrase_lemma,
            "resolution_status": resolution_status,
        }
        return SurfaceNodeSeed(
            node_kind="reference_expr",
            source_ref=source_ref,
            local_discriminator=local,
            rule_id=_RULE_EXPR,
            status=resolution_status,
            payload=payload,
            authority_role="surface_fact",
        )

    @staticmethod
    def _reference_resolution_seed(
        ref: AnaphoricRef,
        *,
        source_ref: SourceSpanRef,
        resolution_status: str,
        local: str,
    ) -> SurfaceNodeSeed:
        """The resolution node: what the anaphor POINTS BACK to (the antecedent).

        Carries the anaphor provenance: surface, head kind, resolution status,
        and the bound ``target_provision_ref`` (None for AMBIGUOUS / OPEN). For
        AMBIGUOUS the candidate list rides the payload — the lens never picks.
        """
        payload: dict[str, object] = {
            "resolution_status": resolution_status,
            "anaphor_surface": ref.surface_text,
            "head_kind": ref.head_kind.value,
            "target_provision_ref": _target_provision_ref(ref),
        }
        if ref.status is AnaphorStatus.AMBIGUOUS:
            payload["candidates"] = _candidate_ids(ref)
        return SurfaceNodeSeed(
            node_kind="reference_resolution",
            source_ref=source_ref,
            local_discriminator=local,
            rule_id=_RULE_RESOLUTION,
            status=resolution_status,
            payload=payload,
            authority_role="surface_fact",
        )
