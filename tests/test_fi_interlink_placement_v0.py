"""Placement-v0 tests: source-occurrence grouping + fail-closed placement ladder.

Covers the grammar9/grammar10 rulings: a set-valued reference expression (range or
coordination) is ONE source occurrence with a resolution_set of all-meant members,
placed ONCE onto the rendered text; the viewer renders one anchor per occurrence.
"""
from __future__ import annotations

import json

from lawvm.tools.transition_graph_interlinks import (
    LawvmInterlinkRow,
    RenderedTextSegment,
    place_lawvm_interlinks,
    place_occurrence_spans,
    placement_summary,
    surface_occurrence_id,
)


def _row(
    *,
    interlink_id: str,
    surface_text: str,
    source_locator: str,
    target_locator: str,
    span_offset: int,
    span_len: int,
    source_work_id: str = "fi:normative_act:2004/301",
    surface_kind: str = "prose_ref",
    rendered_address: str | None = None,
) -> LawvmInterlinkRow:
    return LawvmInterlinkRow.from_mapping(
        {
            "interlink_id": interlink_id,
            "source_jurisdiction": "fi",
            "source_work_kind": "normative_act",
            "source_local_id": "2004/301",
            "source_work_id": source_work_id,
            "source_locator": source_locator,
            "surface_text": surface_text,
            "surface_kind": surface_kind,
            "role": "cites",
            "target_jurisdiction": "fi",
            "target_work_kind": "normative_act",
            "target_local_id": "2004/301",
            "target_work_id": source_work_id,
            "target_locator": target_locator,
            "target_url": None,
            "candidate_work_ids": None,
            "resolution_status": "resolved",
            "confidence": "exact",
            "resolver_id": "fi.reference_mention",
            "source_artifact_id": "2004/301",
            "source_span_byte_offset": span_offset,
            "source_span_byte_len": span_len,
            "rendered_statute_id": None,
            "rendered_effective_date": None,
            "rendered_address": rendered_address,
            "rendered_segment_index": None,
            "rendered_char_start": None,
            "rendered_char_end": None,
            "valid_at_start": None,
            "valid_at_end": None,
            "detail_json": "{}",
        }
    )


# ── surface_occurrence_id grouping ─────────────────────────────────────────


def test_occurrence_id_groups_by_source_span_not_surface_string() -> None:
    """The same string at different spans is distinct; same span groups together."""
    a = surface_occurrence_id(
        work_id="w", source_locator="section:28a",
        source_span_byte_offset=156628, source_span_byte_len=12,
        surface_text="69 c §:ssä",
    )
    b = surface_occurrence_id(
        work_id="w", source_locator="section:69e",
        source_span_byte_offset=160235, source_span_byte_len=12,
        surface_text="69 c §:ssä",
    )
    same = surface_occurrence_id(
        work_id="w", source_locator="section:28a",
        source_span_byte_offset=156628, source_span_byte_len=12,
        surface_text="69 c §:ssä",
    )
    assert a != b           # different span/locator → different occurrence
    assert a == same        # identical key → same occurrence


def test_occurrence_id_normalizes_nbsp_and_dash() -> None:
    nbsp = surface_occurrence_id(
        work_id="w", source_locator="section:1",
        source_span_byte_offset=10, source_span_byte_len=5,
        surface_text="28 tai",
    )
    space = surface_occurrence_id(
        work_id="w", source_locator="section:1",
        source_span_byte_offset=10, source_span_byte_len=5,
        surface_text="28 tai",
    )
    assert nbsp == space


def test_occurrence_id_fallback_ordinal_when_no_span() -> None:
    first = surface_occurrence_id(
        work_id="w", source_locator="section:1",
        source_span_byte_offset=None, source_span_byte_len=None,
        surface_text="69 §", fallback_ordinal=0,
    )
    second = surface_occurrence_id(
        work_id="w", source_locator="section:1",
        source_span_byte_offset=None, source_span_byte_len=None,
        surface_text="69 §", fallback_ordinal=1,
    )
    assert first != second  # span-less occurrences disambiguated by ordinal


# ── placement ladder ───────────────────────────────────────────────────────


def test_ladder_exact_unique() -> None:
    segs = {
        "2020-01-01": [
            RenderedTextSegment("2020-01-01", "section:1", 0, "katso 5 § ja muuta")
        ]
    }
    placements = place_occurrence_spans("5 §", "section:1", segs)
    assert len(placements) == 1
    p = placements[0]
    assert p.placement_status == "placed_exact_unique"
    assert (p.char_start, p.char_end) == (6, 9)


def test_ladder_normalized_unique_nbsp_maps_to_exact_coords() -> None:
    # Source surface has NBSP; rendered image has a plain space.
    text = "mitä 28 tai 69 c §:ssä säädetään"
    segs = {"2020-01-01": [RenderedTextSegment("2020-01-01", "section:28a", 0, text)]}
    placements = place_occurrence_spans("28 tai 69 c §:ssä", "section:28a", segs)
    assert len(placements) == 1
    p = placements[0]
    assert p.placement_status == "placed_normalized_unique"
    # Painted coordinates are EXACT rendered coords, not normalized coords.
    assert text[p.char_start:p.char_end] == "28 tai 69 c §:ssä"


def test_ladder_ambiguous_not_painted_by_default() -> None:
    # Two identical occurrences in scope, no context disambiguation, no ordinal.
    text = "5 § ... ja jälleen 5 § ..."
    segs = {"2020-01-01": [RenderedTextSegment("2020-01-01", "section:1", 0, text)]}
    placements = place_occurrence_spans("5 §", "section:1", segs)
    assert len(placements) == 1
    assert placements[0].placement_status == "unplaced_ambiguous"
    assert placements[0].char_start == -1  # not painted


def test_ladder_absent_yields_no_placement() -> None:
    segs = {"2020-01-01": [RenderedTextSegment("2020-01-01", "section:1", 0, "no refs here")]}
    assert place_occurrence_spans("5 §", "section:1", segs) == []


def test_ladder_ordinal_only_behind_experimental_flag() -> None:
    text = "5 § ... ja jälleen 5 § ..."
    segs = {"2020-01-01": [RenderedTextSegment("2020-01-01", "section:1", 0, text)]}
    placements = place_occurrence_spans(
        "5 §", "section:1", segs, enable_ordinal_experimental=True
    )
    assert len(placements) == 1
    assert placements[0].placement_status == "placed_ordinal_experimental"
    assert placements[0].char_start == 0  # first occurrence


# ── end-to-end: the 2004/301 §28a/3 case ───────────────────────────────────


def _the_2004_301_28a_rows() -> list[LawvmInterlinkRow]:
    # Mirrors the real extraction shape probed from 2004/301:
    #   "28 tai 69 c §:ssä" → coordination, 2 targets, span (156620, 20)
    #   "69 d–69 g §:ssä"   → range, 4 targets,       span (160133, 19)
    #   "69 c §:ssä"        → singleton,              span (156628, 12)
    return [
        _row(interlink_id="fi.refs:2004_301:309", surface_text="28 tai 69 c §:ssä",
             source_locator="section:28a", target_locator="section:28",
             span_offset=156620, span_len=20),
        _row(interlink_id="fi.refs:2004_301:310", surface_text="28 tai 69 c §:ssä",
             source_locator="section:28a", target_locator="section:69c",
             span_offset=156620, span_len=20),
        _row(interlink_id="fi.refs:2004_301:311", surface_text="69 d–69 g §:ssä",
             source_locator="section:28a", target_locator="section:69d",
             span_offset=160133, span_len=19),
        _row(interlink_id="fi.refs:2004_301:312", surface_text="69 d–69 g §:ssä",
             source_locator="section:28a", target_locator="section:69e",
             span_offset=160133, span_len=19),
        _row(interlink_id="fi.refs:2004_301:313", surface_text="69 d–69 g §:ssä",
             source_locator="section:28a", target_locator="section:69f",
             span_offset=160133, span_len=19),
        _row(interlink_id="fi.refs:2004_301:314", surface_text="69 d–69 g §:ssä",
             source_locator="section:28a", target_locator="section:69g",
             span_offset=160133, span_len=19),
        _row(interlink_id="fi.refs:2004_301:315", surface_text="69 c §:ssä",
             source_locator="section:28a", target_locator="section:69c",
             span_offset=156628, span_len=12),
    ]


_28A_TEXT = (
    "Mitä 28 tai 69 c §:ssä säädetään, sovelletaan myös siihen, "
    "mitä 69 d–69 g §:ssä tarkoitetaan."
)


def _placed_28a() -> list[LawvmInterlinkRow]:
    segs = {
        "2010-01-01": [
            RenderedTextSegment("2010-01-01", "section:28a/subsection:3", 0, _28A_TEXT)
        ]
    }
    return place_lawvm_interlinks(
        _the_2004_301_28a_rows(), statute_id="2004/301", segments_by_date=segs
    )


def test_range_collapses_to_one_anchor_with_four_members() -> None:
    placed = _placed_28a()
    rendered = [r for r in placed if r.rendered_address]
    # One anchor per occurrence (3 occurrences, one date) — NOT 7 per-target rows.
    assert len(rendered) == 3

    by_surface = {r.surface_text: r for r in rendered}
    range_row = by_surface["69 d–69 g §:ssä"]
    detail = json.loads(range_row.detail_json)
    rs = json.loads(detail["resolution_set_json"])
    assert rs["kind"] == "finite_all_members"
    members = [m["target_locator"] for m in rs["members"]]
    assert members == ["section:69d", "section:69e", "section:69f", "section:69g"]
    # The painted span is exactly the range surface text.
    assert _28A_TEXT[range_row.rendered_char_start:range_row.rendered_char_end] == "69 d–69 g §:ssä"
    assert detail["placement_status"] == "placed_exact_unique"


def test_coordination_collapses_to_one_anchor_with_two_members() -> None:
    placed = _placed_28a()
    coord = next(r for r in placed if r.rendered_address and r.surface_text.startswith("28"))
    detail = json.loads(coord.detail_json)
    rs = json.loads(detail["resolution_set_json"])
    assert rs["kind"] == "finite_all_members"
    assert [m["target_locator"] for m in rs["members"]] == ["section:28", "section:69c"]
    # NBSP source surface placed over plain-space rendered text via normalization.
    assert detail["placement_status"] == "placed_normalized_unique"
    assert _28A_TEXT[coord.rendered_char_start:coord.rendered_char_end] == "28 tai 69 c §:ssä"


def test_singleton_69c_places() -> None:
    placed = _placed_28a()
    singleton = next(
        r for r in placed if r.rendered_address and r.surface_text == "69 c §:ssä"
    )
    detail = json.loads(singleton.detail_json)
    rs = json.loads(detail["resolution_set_json"])
    assert rs["kind"] == "singleton"
    assert [m["target_locator"] for m in rs["members"]] == ["section:69c"]
    assert detail["placement_status"].startswith("placed_")


def test_placement_summary_counts_occurrences() -> None:
    summary = placement_summary(_placed_28a())
    assert summary["total_occurrences"] == 3
    assert summary["placed_exact_unique"] == 2      # range + singleton
    assert summary["placed_normalized_unique"] == 1  # coordination (NBSP)
    assert summary["unplaced_ambiguous"] == 0


def test_every_row_carries_grouping_fields() -> None:
    placed = _placed_28a()
    for row in placed:
        detail = json.loads(row.detail_json)
        assert detail.get("surface_occurrence_id")
        assert detail.get("resolution_kind") in {
            "singleton", "finite_all_members", "open"
        }
        assert "resolution_set_json" in detail
