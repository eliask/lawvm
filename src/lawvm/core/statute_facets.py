"""Core helpers for statute-level facets that are outside the body tree."""

from __future__ import annotations

from lawvm.core.ir import IRStatute, LegalAddress
from lawvm.core.semantic_types import FacetKind


def statute_title_address() -> LegalAddress:
    """Return the canonical whole-statute title facet address."""
    return LegalAddress(path=(), special=FacetKind.HEADING)


def is_statute_title_address(address: LegalAddress) -> bool:
    """Return True for the whole-statute title/heading facet."""
    return address == statute_title_address()


def replace_statute_title(statute: IRStatute, title: str) -> IRStatute:
    """Return a statute copy with the title facet replaced and body preserved."""
    return IRStatute(
        statute_id=statute.statute_id,
        title=title,
        body=statute.body,
        supplements=statute.supplements,
        metadata=statute.metadata,
    )
