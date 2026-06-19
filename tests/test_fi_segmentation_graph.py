"""Tests for the additive STRUCTURAL Finnish SegmentationGraph.

Covers each segment kind on real Finnish statutory text (chapeau + enumerated
list with chapeau inheritance; a definition list; a heading; a quoted-amendment
block; prose), the KILLER total-token-ownership invariant (the segments
partition the text exactly — no gap/overlap/silent-drop) on several real
statutes, and the span->segment query including the boundary-crossing AMBIGUOUS
case. The carrier-level invariants (partition / earlier-parent link / residual
reason required) are exercised via fail-loud construction.
"""
from __future__ import annotations

import os

import pytest

from lawvm.core.legal_surface_tokens import (
    AMBIGUOUS,
    SegmentationGraph,
    StructuralSegment,
)
from lawvm.finland.legal_surface.clause_segment import build_segmentation_graph


def _seg(text: str) -> SegmentationGraph:
    return build_segmentation_graph("u#body", text)


def _kinds(g: SegmentationGraph) -> list[str]:
    return [s.kind for s in g.segments]


def _content(text: str, g: SegmentationGraph, kind: str) -> list[str]:
    return [text[s.char_start : s.char_end] for s in g.segments if s.kind == kind]


# ── total-token-ownership invariant (synthetic, exact partition) ──────────────


def _assert_partition(text: str, g: SegmentationGraph) -> None:
    """The segments must partition [0, len(text)) exactly (no gap/overlap)."""
    assert g.text_len == len(text)
    cursor = 0
    for s in g.segments:
        assert s.char_start == cursor, (s.char_start, cursor)
        cursor = s.char_end
    assert cursor == len(text)
    # reconstruction: concatenating segment slices reproduces the text exactly
    assert "".join(text[s.char_start : s.char_end] for s in g.segments) == text
    # coverage census is consistent with the partition
    cov = g.coverage()
    assert cov.interpreted_chars + cov.residual_chars == cov.text_len


def test_empty_text_is_a_valid_empty_partition() -> None:
    g = _seg("")
    assert g.segments == ()
    assert g.text_len == 0
    assert g.coverage().interpreted_fraction == 0.0


def test_partition_holds_on_mixed_body() -> None:
    text = (
        "Rikoslaki\n"
        "Suomessa tehtyyn rikokseen sovelletaan Suomen lakia.\n"
        "Käräjäoikeudessa on vain puheenjohtaja:\n"
        "hakemusasiassa, jonka käsittelyä ei jatketa;\n"
        "valmistelussa;\n"
        "riita-asian pääkäsittelyssä.\n"
    )
    _assert_partition(text, _seg(text))


# ── chapeau + enumerated list with inheritance ────────────────────────────────


def test_chapeau_governs_following_list_items() -> None:
    text = (
        "Käräjäoikeudessa on vain puheenjohtaja:\n"
        "hakemusasiassa, jonka käsittelyä ei jatketa;\n"
        "valmistelussa;\n"
        "riita-asian pääkäsittelyssä."
    )
    g = _seg(text)
    _assert_partition(text, g)
    chapeaux = [s for s in g.segments if s.kind == "chapeau"]
    items = [s for s in g.segments if s.kind == "list_item"]
    assert len(chapeaux) == 1
    assert len(items) == 3
    # every list item inherits from the one chapeau (list inheritance)
    chapeau_idx = g.segments.index(chapeaux[0])
    for it in items:
        assert it.parent_index == chapeau_idx
        inherited = g.chapeau_of(it)
        assert inherited is chapeaux[0]
        # the marker is honestly recorded as absent from the <p> coordinate space
        assert "marker_not_in_tape" in it.role


def test_list_closes_on_terminal_period_item() -> None:
    # After the '.'-terminated last item, a subsequent ';'-line is NOT a list item
    # of the same chapeau (the list has closed).
    text = (
        "Lupa myönnetään, jos:\n"
        "hakija täyttää ehdot;\n"
        "toiminta on luvallista.\n"
        "Muu erillinen virke."
    )
    g = _seg(text)
    _assert_partition(text, g)
    items = _content(text, g, "list_item")
    assert items == ["hakija täyttää ehdot;", "toiminta on luvallista."]
    # the trailing line is prose, not a list item
    assert "Muu erillinen virke." in _content(text, g, "prose")


# ── definition list ───────────────────────────────────────────────────────────


def test_definition_chapeau_tags_a_definition_list() -> None:
    text = (
        "Tässä laissa tarkoitetaan:\n"
        "luvalla viranomaisen myöntämää oikeutta;\n"
        "hakijalla luvan hakijaa."
    )
    g = _seg(text)
    _assert_partition(text, g)
    chapeaux = [s for s in g.segments if s.kind == "chapeau"]
    assert len(chapeaux) == 1
    assert chapeaux[0].role == "definition_list"
    items = [s for s in g.segments if s.kind == "list_item"]
    assert items and all(
        it.role == "definition_entry_marker_not_in_tape" for it in items
    )


# ── heading ───────────────────────────────────────────────────────────────────


def test_top_title_lines_are_headings() -> None:
    text = "37/1895\nUlosottolaki\nValtiosäätyjen esityksestä on säädetty:"
    g = _seg(text)
    _assert_partition(text, g)
    headings = _content(text, g, "heading")
    assert "37/1895" in headings
    assert "Ulosottolaki" in headings


def test_long_sentence_line_is_not_a_heading() -> None:
    text = "Suomessa tehtyyn rikokseen sovelletaan Suomen lakia ja muita säädöksiä."
    g = _seg(text)
    _assert_partition(text, g)
    assert _kinds(g) == ["prose"]


# ── quoted-amendment block ────────────────────────────────────────────────────


def test_quoted_amendment_block_after_kuuluu_seuraavasti() -> None:
    text = (
        "Lakiin lisätään uusi 5 a § seuraavasti:\n"
        "Viranomaisen on annettava päätös 30 päivän kuluessa.\n"
        "Päätökseen saa hakea muutosta valittamalla."
    )
    g = _seg(text)
    _assert_partition(text, g)
    chapeaux = [s for s in g.segments if s.kind == "chapeau"]
    assert chapeaux and chapeaux[0].role == "quoted_amendment_chapeau"
    quoted = [s for s in g.segments if s.kind == "quoted_amendment_block"]
    assert len(quoted) == 2
    # the quoted lines inherit (point back to) the lead-in chapeau
    chapeau_idx = g.segments.index(chapeaux[0])
    assert all(q.parent_index == chapeau_idx for q in quoted)


def test_aiempi_sanamuoto_kuuluu_opens_quoted_block() -> None:
    text = (
        "muutettu 5 § tulee voimaan 1.9.2026 Aiempi sanamuoto kuuluu:\n"
        "Käräjäoikeudessa on kolme jäsentä."
    )
    g = _seg(text)
    _assert_partition(text, g)
    assert any(s.kind == "quoted_amendment_block" for s in g.segments)


# ── span -> segment query (mirrors clause_at / sentence_at contract) ──────────


def test_segment_at_returns_enclosing_segment() -> None:
    text = "Käräjäoikeudessa on vain puheenjohtaja:\nvalmistelussa;"
    g = _seg(text)
    start = text.index("valmistelussa")
    end = start + len("valmistelussa")
    seg = g.segment_at(start, end)
    assert isinstance(seg, StructuralSegment)
    assert seg.kind == "list_item"


def test_span_crossing_a_segment_boundary_is_ambiguous() -> None:
    text = "Käräjäoikeudessa on vain puheenjohtaja:\nvalmistelussa;"
    g = _seg(text)
    # a span straddling the chapeau ':' and the list-item line crosses a boundary
    start = text.index("puheenjohtaja")
    end = text.index("valmistelussa") + len("valmistelussa")
    assert g.segment_at(start, end) is AMBIGUOUS


def test_query_in_residual_whitespace_returns_that_residual() -> None:
    text = "Eka rivi\nToka rivi"
    g = _seg(text)
    nl = text.index("\n")
    seg = g.segment_at(nl, nl + 1)  # the newline is a residual segment
    assert isinstance(seg, StructuralSegment)
    assert seg.kind == "residual"
    assert seg.residual_reason == "benign_whitespace"


def test_query_rejects_inverted_span() -> None:
    g = _seg("Eka rivi")
    with pytest.raises(ValueError):
        g.segment_at(5, 2)


# ── carrier fail-loud construction ────────────────────────────────────────────


def test_graph_rejects_partition_gap() -> None:
    with pytest.raises(ValueError):
        SegmentationGraph(
            source_unit_id="u",
            text_hash="h",
            text_len=10,
            segments=(StructuralSegment(0, 4, "prose"),),  # gap [4,10)
        )


def test_graph_rejects_overlap() -> None:
    with pytest.raises(ValueError):
        SegmentationGraph(
            source_unit_id="u",
            text_hash="h",
            text_len=10,
            segments=(
                StructuralSegment(0, 6, "prose"),
                StructuralSegment(4, 10, "prose"),  # overlaps the first
            ),
        )


def test_graph_rejects_forward_parent_link() -> None:
    with pytest.raises(ValueError):
        SegmentationGraph(
            source_unit_id="u",
            text_hash="h",
            text_len=10,
            segments=(
                # list_item whose parent points FORWARD (index 1 >= its own 0)
                StructuralSegment(0, 5, "list_item", parent_index=1),
                StructuralSegment(5, 10, "chapeau"),
            ),
        )


def test_residual_requires_a_reason() -> None:
    with pytest.raises(ValueError):
        StructuralSegment(0, 4, "residual")  # no residual_reason


def test_interpreted_segment_rejects_residual_reason() -> None:
    with pytest.raises(ValueError):
        StructuralSegment(0, 4, "prose", residual_reason="x")


def test_unknown_kind_is_rejected() -> None:
    with pytest.raises(ValueError):
        StructuralSegment(0, 4, "not_a_kind")


# ── real-corpus total-ownership invariant (skips cleanly w/o the archive) ─────


def _real_store_or_skip():
    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT")
    if not root:
        pytest.skip("LAWVM_CANONICAL_DATA_ROOT not set; real-corpus check skipped")
    archive = os.path.join(root, "data", "finlex.farchive")
    if not os.path.exists(archive) or os.path.getsize(archive) < 1_000_000:
        pytest.skip(f"finlex.farchive absent/stub at {archive}; check skipped")
    from farchive import Farchive

    from lawvm.finland.transparent_store import TransparentCorpusStore

    return TransparentCorpusStore(Farchive(archive))


def _read_body_bytes(store, sid: str) -> bytes | None:
    try:
        xb = store.read_oracle(sid)
    except Exception:  # noqa: BLE001
        xb = None
    if xb:
        return xb
    return store.read_source(sid) or store.read_amendment(sid)


def test_total_ownership_on_real_statutes() -> None:
    store = _real_store_or_skip()
    from lawvm.finland.legal_surface.bundle import decode_body_text

    ids = store.list_statute_ids()
    checked = 0
    kinds_seen: set[str] = set()
    for sid in ids:
        xb = _read_body_bytes(store, sid)
        if not xb:
            continue
        text = decode_body_text(xb)
        if len(text) < 300:
            continue
        g = build_segmentation_graph(f"{sid}#body", text)
        # the partition / no-silent-drop invariant on real text
        _assert_partition(text, g)
        # residual is ONLY benign whitespace (no opaque drop class)
        for s in g.segments:
            if s.kind == "residual":
                assert s.residual_reason == "benign_whitespace"
            kinds_seen.add(s.kind)
        checked += 1
        if checked >= 40:
            break
    if checked == 0:
        pytest.skip("no buildable statute bodies in the slice")
    # across 40 real statutes every structural kind should appear at least once
    for expected in ("heading", "chapeau", "list_item", "prose"):
        assert expected in kinds_seen, (expected, kinds_seen)
