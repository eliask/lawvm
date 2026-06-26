"""Project the FULL Legal Surface Graph into viewer-consumable overlay rows.

This is a SIBLING of :mod:`lawvm.finland.legal_surface.projection` (which
projects only the reference lens back to ``fi_refs`` rows). Where that module
reconstructs one lens for a parity gate, this one walks the WHOLE graph (all 9
Finnish lenses + their edges) and projects every *renderable* surface node into
a flat ``lawvm_surface_overlays`` row so a viewer can render rich overlays:
defined terms, term-uses, temporal markers, delegation / sanction / exception
frames, actor/modal frames — not just references.

SPAN BASIS — placeable IDENTICALLY to interlinks
────────────────────────────────────────────────
The viewer places interlinks via the ``rendered_*`` columns that
:func:`lawvm.core.interlinks.legal_interlink_to_row` emits (statute_id,
effective_date, address, segment_index, char_start/char_end) and the
``source_span_byte_*`` columns. Overlay rows carry the EXACT same span columns,
computed from the SAME :class:`~lawvm.core.interlinks.RenderedTextSpan`
machinery, so the viewer can index overlays and interlinks the same way.

A graph node's ``source_ref`` is a CHARACTER anchor into the whole-body
``raw_text`` coordinate (see ``finland.legal_surface.bundle``); its
``char_start``/``char_end`` map directly onto a ``RenderedTextSpan``'s char
bounds. The statute id is ``source_ref.work_id``. ``effective_date`` /
``segment_index`` / ``address`` are NOT carried on the v0 whole-body node anchor,
so they ride a caller-supplied :class:`OverlayRenderedSpanContext`. When the
context cannot supply the fields a valid ``RenderedTextSpan`` requires, the row
gets NULL ``rendered_*`` columns — fail-loud by null, never a fabricated span,
exactly as the interlink path emits null ``rendered_*`` when no rendered span is
known.

SURFACE-FACT DISCIPLINE
───────────────────────
Overlays are typed SURFACE facts (what the text says), never legal conclusions.
Reference / term_use overlays carry the resolution ``status`` so the viewer keeps
its existing status styling. Frame→reference / term_use→definition links are
the graph's own CANDIDATE affordances; they make no legal claim. Ordering is
deterministic (by node_id).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping, Optional

from lawvm.core.interlinks import RenderedTextSpan, legal_interlink_to_row
from lawvm.core.legal_surface_graph import (
    LegalSurfaceGraph,
    SourceSpanRef,
    SurfaceEdge,
    SurfaceNode,
)

# ── Closed overlay vocabulary (the viewer lane codes to these) ────────────────
#
# Maps a graph node_kind -> the overlay ``kind`` the viewer renders. Only these
# node kinds produce overlay rows; everything else (entity handles, the
# reference_resolution that rides on its reference row, surface_residual) is
# skipped.
OVERLAY_KIND_BY_NODE_KIND: Mapping[str, str] = {
    "reference_expr": "reference",
    "definition_binding": "defined_term",
    "term_use": "term_use",
    "temporal_expr": "temporal",
    "delegation_frame": "delegation",
    "procedure_frame": "procedure",
    # Bare process/sanction nouns demoted to cues keep the SAME viewer highlight
    # (procedure / sanction) as their frame siblings — the cue carries the same
    # span + typed sub-kind, so the overlay label is identical.
    "procedure_cue": "procedure",
    "sanction_frame": "sanction",
    "sanction_cue": "sanction",
    "exception_condition_cue": "exception_condition",
    "actor_modal_frame": "actor_modal",
}

#: Closed overlay-kind vocabulary (the values above). Pinned for the viewer lane.
OVERLAY_KINDS: tuple[str, ...] = tuple(
    dict.fromkeys(OVERLAY_KIND_BY_NODE_KIND.values())
)

#: Reference-family overlays whose ``status`` the viewer styles. ``reference``
#: takes its status from the joined ``reference_resolution`` node; ``term_use``
#: from its own node status.
_STATUS_BEARING_KINDS: frozenset[str] = frozenset({"reference", "term_use"})

#: The ``rendered_*`` / ``source_span_byte_*`` column names overlay rows share
#: with interlink rows. Derived from a schema interlink row so the two row
#: shapes can never silently drift (fail loud if the interlink schema changes).
_SHARED_SPAN_COLUMNS: tuple[str, ...] = (
    "source_span_byte_offset",
    "source_span_byte_len",
    "rendered_statute_id",
    "rendered_effective_date",
    "rendered_address",
    "rendered_segment_index",
    "rendered_char_start",
    "rendered_char_end",
)

#: The full overlay row schema (the viewer lane needs these exact names).
OVERLAY_ROW_COLUMNS: tuple[str, ...] = (
    "overlay_id",
    "statute_id",
    "kind",
    "node_id",
    "label",
    "payload_json",
    "links_json",
    "overlay_status",
    *_SHARED_SPAN_COLUMNS,
)


@dataclass(frozen=True)
class OverlayRenderedSpanContext:
    """Caller-supplied context to render-map a node's char anchor.

    The v0 whole-body node anchor carries no ``effective_date`` /
    ``segment_index`` / ``address``. A caller that knows them (a PIT export, the
    viewer lane's test fixture) supplies them here so a valid
    :class:`RenderedTextSpan` can be built. When absent the row gets null
    ``rendered_*`` columns (fail-loud by null).

    * ``effective_date`` — the PIT date the overlay is placed at.
    * ``segment_index``  — render segment within the address (default 0).
    * ``address``        — render address override (else the node's own
      ``source_ref.address``).
    """

    effective_date: Optional[str] = None
    segment_index: int = 0
    address: Optional[str] = None


# ── Shared span machinery (reused from the interlink row projection) ──────────


def _rendered_text_span(
    source_ref: Optional[SourceSpanRef],
    surface_text: str,
    context: Optional[OverlayRenderedSpanContext],
) -> Optional[RenderedTextSpan]:
    """Build a :class:`RenderedTextSpan` from a node's CHAR anchor, or None.

    Mirrors how an interlink row carries its rendered span: a node whose anchor
    cannot be render-mapped (no statute id, degenerate char span, or missing
    effective_date/address/surface_text the context did not supply) yields None
    -> null ``rendered_*`` columns. Never fabricates a span.
    """
    if source_ref is None or context is None:
        return None
    statute_id = source_ref.work_id
    effective_date = context.effective_date
    address = context.address if context.address is not None else source_ref.address
    char_start = source_ref.char_start
    char_end = source_ref.char_end
    # RenderedTextSpan requires non-empty statute_id/effective_date/address and a
    # non-empty surface_text and a non-degenerate char span. Any missing piece =>
    # not render-mappable => null rendered span (fail loud by null).
    if not statute_id or not effective_date or not address or not surface_text:
        return None
    if char_start < 0 or char_end <= char_start:
        return None
    return RenderedTextSpan(
        statute_id=statute_id,
        effective_date=effective_date,
        address=address,
        segment_index=context.segment_index,
        char_start=char_start,
        char_end=char_end,
        surface_text=surface_text,
    )


def _shared_span_columns(
    source_ref: Optional[SourceSpanRef],
    surface_text: str,
    context: Optional[OverlayRenderedSpanContext],
) -> dict[str, object]:
    """Emit the SAME span columns an interlink row carries for this node.

    Reuses :func:`legal_interlink_to_row` so the column names/semantics are
    literally the interlink projection's, never a parallel re-implementation.
    The graph node has no authoritative interlink-byte span on its own anchor
    (that rides the ``reference_expr`` payload, handled in ``_byte_span_from_payload``),
    so ``source_span_byte_*`` come from the payload; the ``rendered_*`` columns
    come from the shared :class:`RenderedTextSpan`.
    """
    from lawvm.core.interlinks import (
        InterlinkConfidence,
        InterlinkResolutionStatus,
        InterlinkRole,
        InterlinkSurfaceKind,
        InterlinkTarget,
        LegalInterlink,
        LegalWorkRef,
    )

    rendered = _rendered_text_span(source_ref, surface_text, context)
    probe = LegalInterlink(
        interlink_id="overlay.span.probe",
        source_work=LegalWorkRef("fi", "normative_act", "span_probe"),
        source_locator=None,
        source_span=None,
        rendered_span=rendered,
        surface_text="span_probe",
        surface_kind=InterlinkSurfaceKind.PROSE_REF,
        target=InterlinkTarget(work=None),
        role=InterlinkRole.UNKNOWN,
        resolution_status=InterlinkResolutionStatus.UNRESOLVED,
        confidence=InterlinkConfidence.LEGACY_UNKNOWN,
        resolver_id="fi.surface_overlay",
    )
    row = legal_interlink_to_row(probe)
    return {col: row[col] for col in _SHARED_SPAN_COLUMNS}


def _byte_span_from_payload(payload: Mapping[str, object]) -> tuple[object, object]:
    """Pull the authoritative interlink byte span off a node payload, if any.

    Only ``reference_expr`` nodes carry the byte-origin span (stashed by the
    reference lens as ``source_span_byte_offset`` / ``source_span_len``, distinct
    from the char-coord ``source_ref``). Returns ``(byte_offset, byte_len)`` or
    ``(None, None)``. Never fabricated.
    """
    offset = payload.get("source_span_byte_offset")
    length = payload.get("source_span_len")
    return (
        offset if isinstance(offset, int) else None,
        length if isinstance(length, int) else None,
    )


# ── Resolution join (reference_expr -> its reference_resolution) ──────────────


def _resolution_by_expr(graph: LegalSurfaceGraph) -> dict[str, SurfaceNode]:
    """Map each ``reference_expr`` node_id -> its ``reference_resolution`` node.

    The lens mints one ``resolution_of`` edge (resolution -> expr) per mention;
    invert it so a reference overlay can read its resolution outcome/status. This
    is the same inversion :func:`projection._resolution_index` performs.
    """
    resolutions = {
        nid: node
        for nid, node in graph.nodes.items()
        if node.node_kind == "reference_resolution"
    }
    by_expr: dict[str, SurfaceNode] = {}
    for edge in graph.edges:
        if edge.edge_kind != "resolution_of":
            continue
        resolution = resolutions.get(edge.src)
        if resolution is not None:
            by_expr[edge.dst] = resolution
    return by_expr


# ── Per-node derivations (label / status) ────────────────────────────────────


def _node_surface_text(node: SurfaceNode) -> str:
    """The node's own surface string (used both as the rendered surface_text and
    as a label fallback). Empty when the node carries none."""
    payload = node.payload
    for key in (
        "surface_text",
        "term_surface",
        "term",
        "marker_surface",
        "marker_text",
        "actor_surface",
    ):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _overlay_label(node: SurfaceNode, kind: str) -> str:
    """Short display string per kind. Surface fact, never a conclusion."""
    payload = node.payload
    if kind == "defined_term":
        term = payload.get("term")
        if isinstance(term, str) and term:
            return term
    if kind == "term_use":
        for key in ("term_surface", "lemma"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
    if kind == "temporal":
        for key in ("surface_text", "temporal_kind"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
    if kind == "delegation":
        instrument = payload.get("instrument_kind")
        if isinstance(instrument, str) and instrument:
            return instrument
    if kind == "procedure":
        value = payload.get("process_kind")
        if isinstance(value, str) and value:
            return value
    if kind == "sanction":
        for key in ("sanction_kind", "marker_surface"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
    if kind == "exception_condition":
        for key in ("cue_kind", "marker_text"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
    if kind == "actor_modal":
        actor = payload.get("actor_surface")
        if isinstance(actor, str) and actor:
            return actor
    # reference + universal fallback: any surface string, then the node kind.
    surface = _node_surface_text(node)
    return surface or node.node_kind


def _overlay_status(
    node: SurfaceNode,
    kind: str,
    resolution: Optional[SurfaceNode],
) -> Optional[str]:
    """Resolution status for status-bearing kinds; else None.

    ``reference`` takes the joined ``reference_resolution``'s ``resolution_status``
    (falling back to the expr node's own status), so the viewer keeps its
    existing reference status styling. ``term_use`` carries its own node status.
    """
    if kind not in _STATUS_BEARING_KINDS:
        return None
    if kind == "reference":
        if resolution is not None:
            res_status = resolution.payload.get("resolution_status")
            if isinstance(res_status, str) and res_status:
                return res_status
        return str(node.node_status) if node.node_status else None
    # term_use
    return str(node.node_status) if node.node_status else None


# ── Stable identity ───────────────────────────────────────────────────────────


def _overlay_id(statute_id: str, node_id: str) -> str:
    """Stable sha over statute_id + node_id (survives payload improvement, since
    node_id is the graph's stable surface identity)."""
    digest = hashlib.sha256(f"{statute_id}\x1f{node_id}".encode("utf-8")).hexdigest()
    return f"fi.overlay:{digest[:32]}"


# ── Links (this node's outgoing edges) ────────────────────────────────────────


def _links_json(
    node_id: str,
    outgoing_edges_by_node: Mapping[str, list[SurfaceEdge]],
    overlay_id_by_node: Mapping[str, str],
) -> str:
    """Serialize this node's OUTGOING edges as a deterministic link list.

    Each link is ``{rel: <edge_kind>, target_overlay_id | target_node_id}`` —
    ``target_overlay_id`` when the edge's destination is itself a renderable
    overlay (so the viewer can hop overlay->overlay, e.g. term_use ->
    definition_binding, frame -> reference_expr), else ``target_node_id`` for an
    entity/resolution endpoint with no own overlay. Sorted for determinism.
    """
    links: list[dict[str, object]] = []
    for edge in outgoing_edges_by_node.get(node_id, ()):
        target_overlay_id = overlay_id_by_node.get(edge.dst)
        link: dict[str, object] = {"rel": edge.edge_kind}
        if target_overlay_id is not None:
            link["target_overlay_id"] = target_overlay_id
        else:
            link["target_node_id"] = edge.dst
        links.append(link)
    links.sort(
        key=lambda lnk: (
            str(lnk["rel"]),
            str(lnk.get("target_overlay_id") or ""),
            str(lnk.get("target_node_id") or ""),
        )
    )
    return json.dumps(links, ensure_ascii=False, sort_keys=True)


# ── The projection ────────────────────────────────────────────────────────────


def graph_to_overlay_rows(
    graph: LegalSurfaceGraph,
    *,
    rendered_span_context: Optional[OverlayRenderedSpanContext] = None,
) -> list[dict[str, object]]:
    """Project the WHOLE graph into ``lawvm_surface_overlays`` rows.

    One row per RENDERABLE surface node (kinds in :data:`OVERLAY_KINDS`). Entity
    handles, the ``reference_resolution`` (its outcome rides the reference row's
    status + payload), and ``surface_residual`` produce no row.

    The viewer places overlays via the SAME ``rendered_*`` / ``source_span_byte_*``
    columns interlinks use — built from the SAME :class:`RenderedTextSpan`
    machinery (see :func:`_shared_span_columns`). Pass ``rendered_span_context``
    to populate ``rendered_*`` (effective_date / segment_index / address); without
    it the v0 whole-body anchor cannot be render-mapped and ``rendered_*`` are
    null — exactly as the interlink path emits null ``rendered_*`` when no rendered
    span is known.

    Deterministic ordering: rows are returned sorted by ``node_id``.
    """
    statute_id = graph.subject.work_id or ""
    resolution_by_expr = _resolution_by_expr(graph)

    # First pass: which nodes become overlays, and their stable overlay_id, so
    # links can resolve overlay->overlay targets.
    renderable_nodes = [
        node
        for node in graph.nodes.values()
        if node.node_kind in OVERLAY_KIND_BY_NODE_KIND
    ]
    overlay_id_by_node: dict[str, str] = {
        node.node_id: _overlay_id(statute_id, node.node_id)
        for node in renderable_nodes
    }
    outgoing_edges_by_node: dict[str, list[SurfaceEdge]] = {}
    for edge in graph.edges:
        outgoing_edges_by_node.setdefault(edge.src, []).append(edge)

    rows: list[dict[str, object]] = []
    for node in renderable_nodes:
        kind = OVERLAY_KIND_BY_NODE_KIND[node.node_kind]
        surface_text = _node_surface_text(node)
        resolution = (
            resolution_by_expr.get(node.node_id)
            if node.node_kind == "reference_expr"
            else None
        )

        span_cols = _shared_span_columns(
            node.source_ref, surface_text, rendered_span_context
        )
        byte_offset, byte_len = _byte_span_from_payload(node.payload)
        # The reference byte-origin span is the node's own; keep it even when no
        # rendered span could be mapped (matches the interlink reference row).
        span_cols["source_span_byte_offset"] = byte_offset
        span_cols["source_span_byte_len"] = byte_len

        row: dict[str, object] = {
            "overlay_id": overlay_id_by_node[node.node_id],
            "statute_id": statute_id,
            "kind": kind,
            "node_id": node.node_id,
            "label": _overlay_label(node, kind),
            "payload_json": json.dumps(
                dict(node.payload), ensure_ascii=False, sort_keys=True, default=str
            ),
            "links_json": _links_json(
                node.node_id,
                outgoing_edges_by_node,
                overlay_id_by_node,
            ),
            "overlay_status": _overlay_status(node, kind, resolution),
            **span_cols,
        }
        rows.append(row)

    rows.sort(key=lambda r: str(r["node_id"]))
    return rows
