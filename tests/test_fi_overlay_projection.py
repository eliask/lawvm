"""Tests for the FULL Legal Surface Graph -> ``lawvm_surface_overlays`` projection.

Builds synthetic Finnish statutes through ``build_legal_surface_graph`` (all 9
lenses + edges) and asserts ``graph_to_overlay_rows`` emits one row per
renderable kind, with the pinned schema: closed-vocab ``kind``, non-empty
``payload_json``, ``links_json`` carrying a term_use->definition / frame->
reference link, reference rows carrying ``status``, deterministic ordering, and
the interlink-shared rendered-span columns (null without a render context, a
real ``RenderedTextSpan`` with one).
"""
from __future__ import annotations

import json

import pytest

from lawvm.finland.legal_surface.graph_build import build_legal_surface_graph
from lawvm.finland.legal_surface.overlay_projection import (
    OVERLAY_KINDS,
    OVERLAY_ROW_COLUMNS,
    OverlayRenderedSpanContext,
    graph_to_overlay_rows,
)


def _xml(*paragraphs: str) -> bytes:
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    return (
        '<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
        f"<body>{body}</body></akomaNtoso>"
    ).encode("utf-8")


# A statute that exercises every renderable lens family:
#   * a parenthetical definition + a later use of that term (defined_term + term_use)
#   * a commencement temporal expression (temporal)
#   * a delegation frame (asetuksella ... voidaan antaa)  (delegation)
#   * a sanction frame (tuomitaan sakkoon)                (sanction)
#   * an exception/condition cue (Jollei toisin saadeta)  (exception_condition)
#   * an actor/modal frame (Viranomaisen on ...)          (actor_modal)
_RICH_STATUTE = _xml(
    "Asetus (EY) N:o 1069/2009 (sivutuoteasetus) on voimassa.",
    "Sivutuoteasetus toimii hyvin. Tama laki tulee voimaan 1 paivana tammikuuta 2020.",
    "Viranomaisen on annettava paatos. Valtioneuvoston asetuksella voidaan antaa "
    "tarkempia saannoksia. Rikkomuksesta tuomitaan sakkoon. Jollei toisin saadeta, "
    "sovelletaan tata lakia.",
)


def _build_rows(**kwargs):
    graph = build_legal_surface_graph(_RICH_STATUTE, "2020/1")
    return graph, graph_to_overlay_rows(graph, **kwargs)


def test_one_overlay_per_renderable_kind_present() -> None:
    _, rows = _build_rows()
    kinds = {r["kind"] for r in rows}
    # Every kind we deliberately authored into the statute must appear.
    for expected in (
        "defined_term",
        "term_use",
        "temporal",
        "delegation",
        "sanction",
        "exception_condition",
        "actor_modal",
        "reference",
    ):
        assert expected in kinds, f"missing overlay kind: {expected} (got {sorted(kinds)})"
    # No row carries a kind outside the closed vocab.
    assert kinds <= set(OVERLAY_KINDS)


def test_rows_honor_pinned_schema_and_nonempty_payload() -> None:
    _, rows = _build_rows()
    assert rows, "expected overlay rows"
    for row in rows:
        assert set(row.keys()) == set(OVERLAY_ROW_COLUMNS)
        assert row["statute_id"] == "2020/1"
        assert row["kind"] in OVERLAY_KINDS
        assert row["node_id"]
        assert row["overlay_id"].startswith("fi.overlay:")
        # payload_json is non-empty parseable JSON (the node's typed facts).
        payload = json.loads(row["payload_json"])
        assert isinstance(payload, dict) and payload
        # label is a non-empty display string.
        assert isinstance(row["label"], str) and row["label"]


def test_reference_rows_carry_resolution_status() -> None:
    _, rows = _build_rows()
    refs = [r for r in rows if r["kind"] == "reference"]
    assert refs, "expected at least one reference overlay"
    for ref in refs:
        assert ref["status"], "reference overlay must carry a resolution status"


def test_term_use_resolves_to_definition_link() -> None:
    _, rows = _build_rows()
    by_id = {r["overlay_id"]: r for r in rows}
    term_use_rows = [r for r in rows if r["kind"] == "term_use"]
    assert term_use_rows, "expected a term_use overlay"
    # The term_use must link to its definition_binding overlay via uses_term
    # (or the cross-lens term_use_resolves_to closure edge).
    found = False
    for tu in term_use_rows:
        for link in json.loads(tu["links_json"]):
            if link["rel"] in ("uses_term", "term_use_resolves_to"):
                target = by_id.get(link.get("target_overlay_id"))
                if target is not None and target["kind"] == "defined_term":
                    found = True
    assert found, "term_use overlay must link to its defined_term overlay"


def test_frame_contains_reference_link() -> None:
    _, rows = _build_rows()
    by_id = {r["overlay_id"]: r for r in rows}
    frame_kinds = {"delegation", "sanction", "exception_condition", "actor_modal"}
    found = False
    for row in rows:
        if row["kind"] not in frame_kinds:
            continue
        for link in json.loads(row["links_json"]):
            if link["rel"] == "frame_contains_reference":
                target = by_id.get(link.get("target_overlay_id"))
                if target is not None and target["kind"] == "reference":
                    found = True
    assert found, "a frame overlay must carry a frame_contains_reference link to a reference overlay"


def test_links_target_overlay_or_node_id() -> None:
    _, rows = _build_rows()
    overlay_ids = {r["overlay_id"] for r in rows}
    for row in rows:
        for link in json.loads(row["links_json"]):
            assert "rel" in link
            # Exactly one of the two target keys is present.
            has_overlay = "target_overlay_id" in link
            has_node = "target_node_id" in link
            assert has_overlay != has_node
            if has_overlay:
                assert link["target_overlay_id"] in overlay_ids


def test_determinism() -> None:
    graph = build_legal_surface_graph(_RICH_STATUTE, "2020/1")
    rows_a = graph_to_overlay_rows(graph)
    rows_b = graph_to_overlay_rows(graph)
    assert rows_a == rows_b
    # Sorted by node_id.
    node_ids = [r["node_id"] for r in rows_a]
    assert node_ids == sorted(node_ids)
    # Rebuilding the graph from the same bytes reproduces the same rows.
    graph2 = build_legal_surface_graph(_RICH_STATUTE, "2020/1")
    assert graph_to_overlay_rows(graph2) == rows_a


def test_rendered_span_null_without_context() -> None:
    _, rows = _build_rows()
    # v0 whole-body anchor: no effective_date/address -> null rendered_* (fail
    # loud by null, never fabricated), matching the interlink export.
    for row in rows:
        assert row["rendered_statute_id"] is None
        assert row["rendered_effective_date"] is None
        assert row["rendered_char_start"] is None


def test_rendered_span_populated_with_context() -> None:
    ctx = OverlayRenderedSpanContext(
        effective_date="2020-01-01", segment_index=0, address="sec_1"
    )
    _, rows = _build_rows(rendered_span_context=ctx)
    # At least one node has a non-degenerate char span and a surface_text, so it
    # maps to a real RenderedTextSpan via the shared interlink machinery.
    mapped = [r for r in rows if r["rendered_char_start"] is not None]
    assert mapped, "expected at least one render-mapped overlay with the context"
    for row in mapped:
        assert row["rendered_statute_id"] == "2020/1"
        assert row["rendered_effective_date"] == "2020-01-01"
        assert row["rendered_address"] == "sec_1"
        assert row["rendered_segment_index"] == 0
        assert row["rendered_char_end"] > row["rendered_char_start"]


def test_reference_overlay_carries_byte_span() -> None:
    _, rows = _build_rows()
    refs = [r for r in rows if r["kind"] == "reference"]
    assert refs
    # The reference lens stashes the authoritative byte-origin span; at least one
    # reference overlay carries it (the others may be metadata-derived -> null).
    assert any(r["source_span_byte_offset"] is not None for r in refs)


def test_clean_statute_emits_only_renderable_rows() -> None:
    # A statute with a definition that is never used: defined_term present, no
    # entity/resolution/residual rows leak in.
    graph = build_legal_surface_graph(
        _xml("Asetus (EY) N:o 1069/2009 (sivutuoteasetus) on voimassa."),
        "2020/2",
    )
    rows = graph_to_overlay_rows(graph)
    kinds = {r["kind"] for r in rows}
    assert "defined_term" in kinds
    assert kinds <= set(OVERLAY_KINDS)
    # term_symbol_entity / surface_residual / reference_resolution never appear.
    assert "term_symbol_entity" not in kinds
    assert "surface_residual" not in kinds


# ── Real-corpus smoke (skips if the Finland archive is absent) ────────────────


def test_real_corpus_smoke_if_archive_present() -> None:
    try:
        from lawvm.finland.corpus import get_corpus_store
    except Exception:  # pragma: no cover - import-time archive wiring
        pytest.skip("Finland corpus store unavailable")

    try:
        store = get_corpus_store()
    except Exception:
        pytest.skip("Finland corpus archive not available")

    from lawvm.tools.export_fi_interlinks import _project_overlays_for_statute
    from lawvm.tools.export_parquet import _load_corpus

    try:
        corpus = _load_corpus("all")
    except Exception:
        pytest.skip("Finland corpus index not available")
    if not corpus:
        pytest.skip("Finland corpus empty")

    # Project overlays for the first statute that yields any; assert schema only.
    for _, statute_id in corpus[:50]:
        rows = list(_project_overlays_for_statute(statute_id, store).rows)
        if rows:
            for row in rows:
                assert set(row.keys()) == set(OVERLAY_ROW_COLUMNS)
                assert row["kind"] in OVERLAY_KINDS
            return
    pytest.skip("no overlays produced in the sampled corpus slice")
