"""Surface-overlay projection and rendered-span placement for transition-graph exports.

The viewer renders rich semantic overlays (defined terms, term-uses, temporal
markers, delegation / sanction / exception frames, actor/modal frames,
references) by painting them inline over the rendered body. It places each
overlay via the SAME ``rendered_address`` / ``rendered_segment_index`` /
``rendered_char_*`` columns it uses for interlinks (see
``viewer/statute-timeline.js`` ``indexSurfaceOverlays`` /
``renderedOverlaysForSegment``) and filters by ``rendered_effective_date`` per
change-date.

A standalone overlay projection (``export_fi_interlinks``) emits NULL
``rendered_*`` because it has no point-in-time rendered segments. The
transition-graph export DOES materialize the tree at every change-date, so it
places overlay rows onto those per-date segments exactly as it places
interlinks — reusing the one neutral span-placement primitive
(``place_surface_text_spans``). The overlay ROW shape and projection are reused
unchanged from :mod:`lawvm.finland.legal_surface.overlay_projection` (the same
``OVERLAY_ROW_COLUMNS`` the standalone export and the viewer code to).

Recognition (the Legal Surface Graph build + projection) stays in jurisdiction
code, supplied via :class:`LawvmSurfaceOverlayExportProvider`; this neutral
module owns only placement and the SQLite-row shape.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any

from lawvm.finland.legal_surface.overlay_projection import OVERLAY_ROW_COLUMNS
from lawvm.tools.transition_graph_interlinks import (
    RenderedTextSegment,
    place_surface_text_spans_many,
)

#: Closed surface-overlay row column order — re-exported from the projection so
#: the transition-graph SQLite shape can never silently drift from the
#: standalone export or the viewer's expectations.
SURFACE_OVERLAY_ROW_COLUMNS: tuple[str, ...] = OVERLAY_ROW_COLUMNS

# A jurisdiction-supplied projector: (statute_id, corpus) -> overlay rows. Each
# row is a flat dict keyed by SURFACE_OVERLAY_ROW_COLUMNS, with NULL rendered_*
# columns (the whole-body projection carries no point-in-time placement).
SurfaceOverlayProjector = Callable[[str, object | None], list[dict[str, object]]]


@dataclasses.dataclass(frozen=True, slots=True)
class LawvmSurfaceOverlayExportProvider:
    """Jurisdiction adapter for the surface-overlay projection.

    Mirrors :class:`~lawvm.tools.transition_graph_interlinks.LawvmInterlinkExportProvider`:
    the jurisdiction owns recognition (building the Legal Surface Graph and
    projecting overlay rows); the neutral exporter owns placement + persistence.
    """

    project_overlays: SurfaceOverlayProjector


def _placed_overlay_row(
    row: dict[str, object],
    *,
    overlay_id: str,
    statute_id: str,
    effective_date: str,
    address: str,
    segment_index: int,
    char_start: int,
    char_end: int,
) -> dict[str, object]:
    placed = dict(row)
    placed["overlay_id"] = overlay_id
    placed["rendered_statute_id"] = statute_id
    placed["rendered_effective_date"] = effective_date
    placed["rendered_address"] = address
    placed["rendered_segment_index"] = segment_index
    placed["rendered_char_start"] = char_start
    placed["rendered_char_end"] = char_end
    return placed


def place_lawvm_surface_overlays(
    rows: list[dict[str, object]],
    *,
    statute_id: str,
    segments_by_date: dict[str, list[RenderedTextSegment]],
) -> list[dict[str, object]]:
    """Place whole-body overlay rows onto the per-date rendered segments.

    Mirrors :func:`~lawvm.tools.transition_graph_interlinks.place_lawvm_interlinks`:
    a row that already carries an explicit ``rendered_address`` is kept as-is; an
    unplaced row is string-matched (by its ``label`` surface) against each
    change-date's rendered segments via the shared
    :func:`place_surface_text_spans`. Every unambiguous date placement yields one
    placed copy with a date-keyed ``overlay_id`` and populated ``rendered_*``
    columns (so the viewer's per-date overlay filter finds it). Overlay graph
    nodes carry no locator, so placement is by surface text alone — exactly the
    empty-locator interlink case. Rows that cannot be placed are kept unplaced
    (null ``rendered_*``); the overlay is still a valid semantic row, just not
    painted inline.
    """
    placed: list[dict[str, object]] = []
    placement_by_surface = place_surface_text_spans_many(
        [
            str(row.get("label") or "")
            for row in rows
            if not row.get("rendered_address")
        ],
        None,
        segments_by_date,
    )
    for row in rows:
        if row.get("rendered_address"):
            placed.append(row)
            continue
        surface_text = str(row.get("label") or "")
        candidates = placement_by_surface.get(surface_text, [])
        if not candidates:
            placed.append(row)
            continue
        base_overlay_id = str(row.get("overlay_id") or "")
        for index, (date, segment, start) in enumerate(candidates):
            placed.append(
                _placed_overlay_row(
                    row,
                    overlay_id=f"{base_overlay_id}:rendered:{index}",
                    statute_id=statute_id,
                    effective_date=date,
                    address=segment.address,
                    segment_index=segment.segment_index,
                    char_start=start,
                    char_end=start + len(surface_text),
                )
            )
    return placed


def overlay_row_sql_values(row: dict[str, object]) -> tuple[Any, ...]:
    """Project an overlay row dict into the SQLite column tuple (fixed order)."""
    return tuple(row.get(col) for col in SURFACE_OVERLAY_ROW_COLUMNS)
