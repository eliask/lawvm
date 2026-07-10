"""Hermetic tests for the amendment-IR corpus driver.

The driver (:func:`run_corpus_diff`) is dependency-injected — it takes a
``router`` (sid → :class:`Route`) and a ``comparer`` (sid, lane, text_fn →
:class:`CompareResult`) — so these tests exercise the full aggregation, JSONL
persistence and the vision-cap discipline WITHOUT the vision backend or the
farchive.  No network, no GPU, no pdfium.
"""
from __future__ import annotations

import json
from pathlib import Path

from lawvm.tools.fi_amendment_ir_compare import CompareResult, OpDivergence
from lawvm.tools.fi_amendment_ir_corpus import (
    LANE_CAP_SKIPPED,
    LANE_GEOM,
    LANE_LOAD_ERROR,
    LANE_VISION,
    Route,
    aggregate_rows,
    run_corpus_diff,
)


def _compared(sid: str, divergences=()) -> CompareResult:
    return CompareResult(
        sid=sid,
        lang="fin",
        compare_status="compared",
        divergences=tuple(divergences),
        xml_op_count=2,
        pdf_op_count=2,
    )


def _missing_div(ref: str) -> OpDivergence:
    return OpDivergence(
        kind="op_missing_in_pdf",
        target_ref=ref,
        xml_op=f"repeal {ref}",
        pdf_op=None,
        detail="dropped",
    )


def _kind_mismatch_div(ref: str) -> OpDivergence:
    return OpDivergence(
        kind="kind_mismatch",
        target_ref=ref,
        xml_op=f"replace {ref}",
        pdf_op=f"insert {ref}",
        detail="kind differs",
    )


def test_driver_aggregates_and_persists(tmp_path: Path) -> None:
    """Routing, per-status folding, exact-match rate and JSONL persistence."""
    sids = ["2001/1", "2001/2", "2001/3", "2001/4"]

    routes = {
        # born-digital → free geom; exact
        "2001/1": Route("2001/1", LANE_GEOM, lambda: "t", 1.0, 3),
        # born-digital → free geom; one genuine typed divergence
        "2001/2": Route("2001/2", LANE_GEOM, lambda: "t", 0.8, 4),
        # scanned → vision; benign pdf_annex_only
        "2001/3": Route("2001/3", LANE_VISION, None, 0.1, 5),
        # unreadable → load_error (never reaches comparer)
        "2001/4": Route("2001/4", LANE_LOAD_ERROR, None, 0.0, 0, "boom"),
    }

    def router(sid: str) -> Route:
        return routes[sid]

    def comparer(sid, lane, text_fn) -> CompareResult:
        if sid == "2001/1":
            return _compared(sid, [OpDivergence("matched", "section:1", "x", "x", "")])
        if sid == "2001/2":
            return _compared(sid, [_missing_div("section:5"), _kind_mismatch_div("section:6")])
        if sid == "2001/3":
            return CompareResult(sid, "fin", "pdf_annex_only", (), 2, 0, "annex")
        raise AssertionError(f"comparer must not be called for {sid}")

    out = tmp_path / "corpus.jsonl"
    report = run_corpus_diff(
        sids, router, comparer, vision_cap=50, out_path=str(out)
    )

    # Lane accounting.
    assert report.n_attempted == 4
    assert report.n_geom == 2
    assert report.n_vision == 1
    assert report.n_load_error == 1
    assert report.n_cap_skipped == 0

    # Status strata.
    assert report.status_counts["compared"] == 2
    assert report.status_counts["pdf_annex_only"] == 1
    assert report.status_counts[LANE_LOAD_ERROR] == 1

    # Over the compared set: 2001/1 exact, 2001/2 has 2 typed divergences.
    assert report.n_compared == 2
    assert report.n_exact == 1
    assert report.exact_match_rate == 0.5
    assert report.total_typed_divergences == 2
    assert report.bucket_counts["op_missing_in_pdf"] == 1
    assert report.bucket_counts["kind_mismatch"] == 1
    assert report.bucket_counts["op_extra_in_pdf"] == 0

    # Worst ranking: only 2001/2 has typed divergences.
    assert [r.sid for r in report.worst] == ["2001/2"]

    # JSONL persistence: one row per statute, in order, with the residual fields.
    persisted = [json.loads(line) for line in out.read_text().splitlines()]
    assert [r["sid"] for r in persisted] == sids
    row2 = next(r for r in persisted if r["sid"] == "2001/2")
    assert row2["lane_used"] == LANE_GEOM
    assert row2["typed_divergence_count"] == 2
    assert {d["kind"] for d in row2["divergences"]} == {"op_missing_in_pdf", "kind_mismatch"}
    load_row = next(r for r in persisted if r["sid"] == "2001/4")
    assert load_row["compare_status"] == LANE_LOAD_ERROR
    assert load_row["detail"] == "boom"


def test_vision_cap_is_honored_and_logged(tmp_path: Path) -> None:
    """Vision statutes past the cap are recorded cap_skipped, never silently dropped;
    born-digital (geom) statutes are UNCAPPED."""
    sids = [f"2002/{i}" for i in range(6)]
    # sids 0,2,4 = geom (free, uncapped); 1,3,5 = vision (capped at 2).
    lanes = {s: (LANE_GEOM if i % 2 == 0 else LANE_VISION) for i, s in enumerate(sids)}

    def router(sid: str) -> Route:
        lane = lanes[sid]
        text_fn = (lambda: "t") if lane == LANE_GEOM else None
        return Route(sid, lane, text_fn, 0.9 if lane == LANE_GEOM else 0.1, 2)

    calls: list[str] = []

    def comparer(sid, lane, text_fn) -> CompareResult:
        calls.append(sid)
        return _compared(sid)

    out = tmp_path / "cap.jsonl"
    report = run_corpus_diff(sids, router, comparer, vision_cap=2, out_path=str(out))

    # 3 geom (uncapped) + 2 vision (up to cap) reach the comparer; the 3rd vision skipped.
    assert report.n_geom == 3
    assert report.n_vision == 2
    assert report.n_cap_skipped == 1
    assert len(calls) == 5  # 3 geom + 2 vision comparisons actually ran
    assert "2002/5" not in calls  # the 3rd vision statute was capped, never compared

    persisted = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(persisted) == 6  # every statute persisted, incl. the cap-skipped one
    skipped = [r for r in persisted if r["lane_used"] == LANE_CAP_SKIPPED]
    assert len(skipped) == 1
    assert skipped[0]["sid"] == "2002/5"
    assert skipped[0]["compare_status"] == LANE_CAP_SKIPPED


def test_aggregate_rows_worst_limit() -> None:
    """The worst ranking honors its limit and is deterministic worst-first."""
    from lawvm.tools.fi_amendment_ir_corpus import _row_from_result

    rows = []
    for i in range(5):
        route = Route(f"2003/{i}", LANE_GEOM, None, 1.0, 1)
        divs = tuple(_missing_div(f"section:{j}") for j in range(i))  # i typed divs
        rows.append(_row_from_result(_compared(f"2003/{i}", divs), route))

    report = aggregate_rows(rows, worst_limit=2)
    # 2003/4 (4 typed) and 2003/3 (3 typed) are the two worst.
    assert [r.sid for r in report.worst] == ["2003/4", "2003/3"]
    assert report.n_compared == 5
    assert report.n_exact == 1  # only 2003/0 has zero typed divergences
