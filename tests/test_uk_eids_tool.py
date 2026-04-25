from __future__ import annotations

from lawvm.tools.uk_eids import _iter_prefixed_rows


def test_iter_prefixed_rows_filters_and_deduplicates() -> None:
    eid_map = {
        "n1": "section-72-4",
        "n2": "section-72-4-c",
        "n3": "section-72-4-c",
        "n4": "section-73-1",
    }
    text_map = {
        "section-72-4": "subsection 4",
        "section-72-4-c": "paragraph c",
        "section-73-1": "other",
    }

    rows = list(_iter_prefixed_rows(eid_map, text_map, prefix="section-72"))

    assert rows == [
        ("section-72-4", "subsection 4"),
        ("section-72-4-c", "paragraph c"),
    ]
