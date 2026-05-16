from __future__ import annotations

from lawvm.new_zealand.dates import nz_date_text_to_iso


def test_nz_date_text_to_iso_normalizes_history_note_dates() -> None:
    assert nz_date_text_to_iso("1 January 2025") == "2025-01-01"
    assert nz_date_text_to_iso("30 June 2022") == "2022-06-30"
    assert nz_date_text_to_iso("2026-04-05") == "2026-04-05"


def test_nz_date_text_to_iso_returns_empty_for_unsupported_shapes() -> None:
    assert nz_date_text_to_iso("") == ""
    assert nz_date_text_to_iso("on commencement") == ""
    assert nz_date_text_to_iso("32 January 2025") == ""
