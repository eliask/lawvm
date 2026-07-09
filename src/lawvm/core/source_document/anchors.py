"""Source anchors for source-document extraction (D0).

A ``SourceAnchor`` pins an extraction assertion to a re-locatable region of a
source manifestation. It is the EXTRACTION-LAYER anchor: pre-validation, and
may be image geometry (page + bbox) OR a text span (locator + byte range) —
digital pages carry text spans, scans carry image geometry. On acceptance it
lowers to a ``lawvm.core.provenance_graph.SourceRef`` (provenance-graph layer,
content-addressed, text-span). Per AGENTS.md §1.12 (no representation
regression), each layer owns its own waist; an anchor is never re-derived from
rendered text after a typed owner exists.

Discipline (AGENTS.md §1.9, §1.10): typed frozen carrier; ``locator`` is
required and non-empty — an anchor that pins no region is useless, and an
unanchorable proposal is rejected by the validators (D5), never silently kept.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True, slots=True)
class BBox:
    """Axis-aligned bounding box in PDF points (origin top-left).

    Used to route a bounded region to a vision/OCR backend (D4) and to validate
    that a proposed fragment's geometry lies inside its page (D5 hard validator).
    """

    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self) -> None:
        if self.x1 < self.x0:
            raise ValueError(f"BBox must satisfy x1 >= x0; got x0={self.x0}, x1={self.x1}")
        if self.y1 < self.y0:
            raise ValueError(f"BBox must satisfy y1 >= y0; got y0={self.y0}, y1={self.y1}")


@dataclass(frozen=True, slots=True)
class SourceAnchor:
    """Re-locatable reference to a region of a source manifestation.

    ``artifact_digest``: SHA-256 of the source manifestation bytes the anchor
        points into (see ``SourceManifestation.artifact_digest``).
    ``locator``: structural locator — REQUIRED, non-empty. For a text region an
        xpath / css / akn selector; for an image region a deterministic
        page+bbox encoding (e.g. ``"page=3;bbox=10.0,20.0,400.0,80.0"``).
        Mirrors the ``SourceRef.structural_locator`` discipline: byte offsets
        alone are fragile across reformats, so a structural locator is mandatory.
    ``page_num`` / ``bbox``: present for image/rendered regions (vision routing).
    ``byte_range``: present for text-span regions into the artifact bytes.

    At least the ``locator`` must be non-empty; the D5 validators reject any
    proposal whose text cannot be re-anchored to a concrete region.
    """

    artifact_digest: str
    locator: str
    page_num: Optional[int] = None
    bbox: Optional[BBox] = None
    byte_range: Optional[Tuple[int, int]] = None

    def __post_init__(self) -> None:
        if not self.artifact_digest:
            raise ValueError("SourceAnchor.artifact_digest must be non-empty")
        if not self.locator:
            raise ValueError(
                "SourceAnchor.locator must be non-empty; an anchor that pins no "
                "region cannot authorize extraction (AGENTS.md §1.10)"
            )
        if self.page_num is not None and self.page_num < 0:
            raise ValueError("SourceAnchor.page_num must be >= 0")
        if self.byte_range is not None:
            start, end = self.byte_range
            if not isinstance(start, int) or not isinstance(end, int):
                raise TypeError("SourceAnchor.byte_range values must be ints")
            if start < 0 or end < start:
                raise ValueError("SourceAnchor.byte_range must satisfy 0 <= start <= end")
