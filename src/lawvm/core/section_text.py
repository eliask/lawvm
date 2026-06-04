"""SectionText — typed per-section oracle text primitive.

Captures the current consolidated oracle text of one section from
a Finlex AKN document. This is a lightweight projection primitive
decoupled from the replay-vs-oracle scoring pipeline.

Per AGENTS.md §1.9: typed dataclass, no stringly-typed fields.
Per brief (SECTIONS_TEXT_PROJECTION.md): frozen + slots=True.

Phase: Phase 3 (Parse) + Phase 4 (Extract).
Jurisdiction: Finland only (fi).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass(frozen=True, slots=True)
class SectionText:
    """One section's oracle text from a consolidated Finlex AKN document.

    statute_id:            Statute ID, e.g. '2003/434'.
    section_key:           AKN structural path key, e.g. 'section:7'
                           or 'chapter:1/section:5'. Derived from eId.
    section_label:         Display number from <num>, e.g. '7 §' or '5 a §'.
    heading_text:          Section title from <heading>, or '' if absent.
    body_text:             Full section body text (no AKN markup).
                           Inline <ref> markup stripped but displayed text kept.
    char_count:            len(body_text).
    source_span_byte_offset: Byte offset in oracle XML where the section element
                           begins. None when not computed.
    source_span_len:       Byte length of the section element. None when not
                           computed.
    valid_at_start:        dateConsolidated from oracle FRBR, or None if absent.
    valid_at_end:          Always None (current consolidated snapshot; per-version
                           history is a separate heavier projection).
    """

    statute_id: str
    section_key: str
    section_label: str
    heading_text: str
    body_text: str
    char_count: int
    source_span_byte_offset: Optional[int]
    source_span_len: Optional[int]
    valid_at_start: Optional[date]
    valid_at_end: Optional[date]


def section_text_to_row(st: SectionText) -> dict:
    """Serialize a SectionText to a flat dict for Parquet/JSONL emission."""
    return {
        "statute_id": st.statute_id,
        "section_key": st.section_key,
        "section_label": st.section_label,
        "heading_text": st.heading_text,
        "body_text": st.body_text,
        "char_count": st.char_count,
        "source_span_byte_offset": st.source_span_byte_offset,
        "source_span_len": st.source_span_len,
        "valid_at_start": st.valid_at_start.isoformat() if st.valid_at_start else None,
        "valid_at_end": st.valid_at_end.isoformat() if st.valid_at_end else None,
    }
