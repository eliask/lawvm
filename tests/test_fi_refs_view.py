"""Unit tests for the ``lawvm fi-refs`` annotated-source-canvas viewer.

All tests run on SYNTHETIC reference dicts / Mark fixtures — no corpus access.
They cover the pure overlay contract (``build_marks``), the counts census, the
clause-window machinery (merge / split), the per-level renderers (one golden
each), and the audit behaviors (``--only`` residue filter, positionless footer,
sigil mapping, self-ref serialization, ``--as-of`` interval filter).
"""

from __future__ import annotations

from typing import Any

import pytest

from lawvm.tools.fi_refs_view import (
    Mark,
    _passes_as_of,
    _serialize_target,
    _sigil,
    build_counts,
    build_marks,
    build_refs_view,
    render_context_view,
    render_counts_view,
    render_digest_view,
    render_full_view,
)


# A small synthetic body the char anchors index into.
BODY = (
    "Tämän lain 5 §:ssä tarkoitettuun toimintaan sovelletaan, "  # 0..56
    "mitä laissa (1054/2018) säädetään. "  # 57..91
    "Jollei muussa laissa toisin säädetä, sovelletaan tätä lakia."  # 92..151
)


def _ref(
    node_id: str,
    *,
    surface: str,
    char_start: int | None,
    char_end: int | None,
    status: str,
    cite_kind: str = "cross_statute",
    target_id: str | None = None,
    target: str | None = None,
    phrase_lemma: str | None = None,
    valid_start: str | None = None,
    valid_end: str | None = None,
) -> dict[str, Any]:
    """Build a renderer-neutral reference record (what ``_ref_dict`` produces)."""
    return {
        "node_id": node_id,
        "surface_text": surface,
        "cite_kind": cite_kind,
        "ref_status": status,
        "phrase_lemma": phrase_lemma,
        "edge_subtype": "xml_ref",
        "target_id": target_id,
        "target_provision_ref": target,
        "source_provision_ref": "2009/953/7",
        "valid_at_start": valid_start,
        "valid_at_end": valid_end,
        "char_start": char_start,
        "char_end": char_end,
        "candidates": [],
    }


def _sample_refs() -> list[dict[str, Any]]:
    return [
        _ref(
            "n1",
            surface="5 §:ssä",
            char_start=11,
            char_end=18,
            status="exact",
            cite_kind="internal",
            target_id="2009/953",
            target="2009/953/5",
        ),
        _ref(
            "n2",
            surface="(1054/2018)",
            char_start=69,
            char_end=80,
            status="exact",
            target_id="2018/1054",
            target="2018/1054",
        ),
        _ref(
            "n3",
            surface="muussa laissa",
            char_start=99,
            char_end=112,
            status="open",
            cite_kind="cross_statute",
            target=None,
        ),
        # positionless metadata edge (REPEALS): no char anchor.
        _ref(
            "n4",
            surface="",
            char_start=None,
            char_end=None,
            status="exact",
            target_id="2006/539",
            target="2006/539",
            phrase_lemma="REPEALS",
        ),
    ]


# ── sigil mapping ─────────────────────────────────────────────────────────────


def test_sigil_mapping_covers_status_vocabulary() -> None:
    assert _sigil("exact") == "="
    assert _sigil("resolved") == "="
    assert _sigil("statute_only") == "~"
    assert _sigil("ambiguous") == "?"
    assert _sigil("open") == "○"
    assert _sigil("broken") == "✗"
    assert _sigil("unresolved") == "⊘"
    assert _sigil(None) == "⊘"
    assert _sigil("garbage") == "⊘"


# ── target serialization (self-ref shorthand vs external) ─────────────────────


def test_self_ref_drops_statute_id_and_leads_with_section() -> None:
    ref = _sample_refs()[0]
    assert _serialize_target(ref, "2009/953") == "§5"


def test_external_ref_keeps_full_target() -> None:
    ref = _sample_refs()[1]
    assert _serialize_target(ref, "2009/953") == "2018/1054"


def test_open_ref_describes_by_status() -> None:
    ref = _sample_refs()[2]
    assert _serialize_target(ref, "2009/953") == "(open: vague catch-all)"


# ── build_marks: canvas vs positionless split ─────────────────────────────────


def test_build_marks_splits_canvas_and_positionless() -> None:
    marks, positionless = build_marks(_sample_refs(), "2009/953")
    assert [m.payload["node_id"] for m in marks] == ["n1", "n2", "n3"]
    # sorted by char position
    assert [m.char_start for m in marks] == [11, 69, 99]
    assert len(positionless) == 1
    assert positionless[0]["family"] == "metadata"
    assert positionless[0]["role"] == "REPEALS"
    # surface text round-trips against the body for canvas marks.
    for m in marks:
        assert BODY[m.char_start : m.char_end] == m.label


def test_positionless_never_dropped_even_when_anchorless() -> None:
    refs = [_ref("only", surface="", char_start=None, char_end=None,
                 status="exact", phrase_lemma="ISSUES")]
    marks, positionless = build_marks(refs, "2009/953")
    assert marks == []
    assert len(positionless) == 1


# ── --only residue filter ─────────────────────────────────────────────────────


def test_only_filter_spotlights_residue() -> None:
    only = frozenset({"open"})
    marks, positionless = build_marks(_sample_refs(), "2009/953", only=only)
    assert [m.payload["node_id"] for m in marks] == ["n3"]
    # the exact REPEALS positionless edge is filtered out by --only open.
    assert positionless == []


# ── --as-of interval filter ───────────────────────────────────────────────────


def test_as_of_drops_refs_outside_interval() -> None:
    inside = _ref("a", surface="x", char_start=0, char_end=1, status="exact",
                  valid_start="2020-01-01", valid_end="2022-01-01")
    after = _ref("b", surface="y", char_start=2, char_end=3, status="exact",
                 valid_start="2024-01-01", valid_end=None)
    assert _passes_as_of(inside, "2021-01-01") is True
    assert _passes_as_of(inside, "2023-01-01") is False
    assert _passes_as_of(after, "2025-01-01") is True
    assert _passes_as_of(after, "2023-01-01") is False
    # no interval → always passes
    assert _passes_as_of(_ref("c", surface="z", char_start=0, char_end=1,
                              status="exact"), "1999-01-01") is True


# ── counts census ─────────────────────────────────────────────────────────────


def test_build_counts_by_family_and_status() -> None:
    marks, positionless = build_marks(_sample_refs(), "2009/953")
    counts = build_counts(marks, positionless)
    assert counts["by_family"] == {"refs": 3, "positionless": 1}
    assert counts["by_status"] == {"exact": 2, "open": 1}
    assert counts["positionless_by_status"] == {"exact": 1}
    assert counts["total"] == 4


# ── golden renders, one per level ─────────────────────────────────────────────


def _view(level: str, **kw: Any) -> dict[str, Any]:
    marks, positionless = build_marks(_sample_refs(), "2009/953")
    counts = build_counts(marks, positionless)
    return build_refs_view(
        "2009/953", BODY, marks, positionless, counts,
        level=level, as_of=None, **kw,
    )


def test_render_counts_golden() -> None:
    out = render_counts_view(_view("counts"))
    assert out.splitlines()[0] == "fi-refs 2009/953  ·  counts  ·  as-of current"
    assert "refs 3   = 2  ~ 0  ? 0  ○ 1  ✗ 0  ⊘ 0   (+1 positionless → footer)" in out


def test_render_digest_golden_residue_first() -> None:
    out = render_digest_view(_view("digest"))
    lines = [ln for ln in out.splitlines() if "«" in ln and "REPEALS" not in ln]
    # The open (residue) ref sorts before the exact ones.
    assert "«muussa laissa»" in lines[0]
    assert "○" in lines[0]
    # positionless footer present.
    assert "REFERENCES WITHOUT A BODY POSITION (1)" in out
    assert "REPEALS" in out


def test_render_context_golden_with_windows() -> None:
    # context needs windows; build them via a one-clause-per-mark stub.
    marks, positionless = build_marks(_sample_refs(), "2009/953")
    counts = build_counts(marks, positionless)
    windows = [{"lo": m.char_start, "hi": m.char_end, "marks": [m]} for m in marks]
    view = build_refs_view(
        "2009/953", BODY, marks, positionless, counts,
        level="context", as_of=None, windows=windows,
    )
    out = render_context_view(view)
    assert "→= «5 §:ssä» ▸ §5" in out
    assert "→= «(1054/2018)» ▸ 2018/1054" in out
    assert "→○ «muussa laissa» ▸ (open: vague catch-all)" in out
    # elision marker between non-adjacent windows.
    assert "⋯" in out


def test_render_full_golden_inline_markers_and_table() -> None:
    out = render_full_view(_view("full"), BODY)
    # inline markers inserted after each surface, in char order.
    assert "5 §:ssä[1]" in out
    assert "(1054/2018)[2]" in out
    assert "muussa laissa[3]" in out
    assert "RESOLUTION TABLE" in out
    assert "[1] →= «5 §:ssä» → §5" in out


def test_zero_refs_renders_honest_empty_not_crash() -> None:
    counts = build_counts([], [])
    view = build_refs_view("9999/9", "", [], [], counts, level="digest", as_of=None)
    out = render_digest_view(view)
    assert "9999/9: 0 references" in out


# ── window merge / split machinery ────────────────────────────────────────────


def test_merge_windows_merges_adjacent_and_keeps_marks() -> None:
    from lawvm.tools.fi_refs_view import _merge_windows

    m1 = Mark("refs", 0, 5, "→", "a", "exact")
    m2 = Mark("refs", 6, 10, "→", "b", "exact")
    m3 = Mark("refs", 100, 105, "→", "c", "open")
    merged = _merge_windows([(0, 5, m1), (5, 10, m2), (100, 105, m3)], merge_gap=0)
    assert len(merged) == 2
    assert merged[0]["lo"] == 0 and merged[0]["hi"] == 10
    assert len(merged[0]["marks"]) == 2
    assert merged[1]["lo"] == 100


def test_window_for_mark_radius_snaps_to_clauses() -> None:
    from lawvm.tools.fi_refs_view import _window_for_mark

    clauses = [(0, 20), (20, 50), (50, 90)]
    m = Mark("refs", 25, 30, "→", "x", "exact")
    assert _window_for_mark(clauses, m, 0) == (20, 50)
    assert _window_for_mark(clauses, m, 1) == (0, 90)


def test_only_unknown_status_fails_loud() -> None:
    from lawvm.tools.fi_refs_view import _parse_only

    with pytest.raises(SystemExit):
        _parse_only("exact,nonsense")
