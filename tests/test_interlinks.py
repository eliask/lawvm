"""Tests for the jurisdiction-neutral legal interlink substrate."""
from __future__ import annotations

from datetime import date

import pytest

from lawvm.core.interlinks import (
    INTERLINK_ROW_COLUMNS,
    InterlinkConfidence,
    InterlinkResolutionStatus,
    InterlinkRole,
    InterlinkSurfaceKind,
    InterlinkTarget,
    LegalInterlink,
    LegalWorkRef,
    RenderedTextSpan,
    legal_interlink_to_row,
)


def test_resolved_interlink_requires_target_work() -> None:
    with pytest.raises(ValueError, match="target.work"):
        LegalInterlink(
            interlink_id="x",
            source_work=LegalWorkRef("zz", "normative_act", "act-1"),
            source_locator=None,
            source_span=None,
            rendered_span=None,
            surface_text="surface",
            surface_kind=InterlinkSurfaceKind.PROSE_REF,
            target=InterlinkTarget(work=None),
            role=InterlinkRole.CITES,
            resolution_status=InterlinkResolutionStatus.RESOLVED,
            confidence=InterlinkConfidence.EXACT,
            resolver_id="test",
        )


def test_interlink_row_columns_are_stable() -> None:
    row = legal_interlink_to_row(
        LegalInterlink(
            interlink_id="l1",
            source_work=LegalWorkRef("zz", "normative_act", "act-1"),
            source_locator=None,
            source_span=None,
            rendered_span=RenderedTextSpan(
                statute_id="act-1",
                effective_date="2024-01-01",
                address="section:1",
                segment_index=0,
                char_start=2,
                char_end=8,
                surface_text="surface",
            ),
            surface_text="surface",
            surface_kind=InterlinkSurfaceKind.PROSE_REF,
            target=InterlinkTarget(work=LegalWorkRef("zz", "normative_act", "act-2")),
            role=InterlinkRole.CITES,
            resolution_status=InterlinkResolutionStatus.RESOLVED,
            confidence=InterlinkConfidence.EXACT,
            resolver_id="test",
            valid_at_interval=(date(2024, 1, 1), None),
        )
    )
    assert tuple(row) == INTERLINK_ROW_COLUMNS
    assert row["rendered_address"] == "section:1"
    assert row["valid_at_start"] == "2024-01-01"
