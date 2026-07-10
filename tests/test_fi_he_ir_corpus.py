"""Hermetic tests for the HE proposed-effect IR corpus driver.

The driver (:func:`run_he_corpus`) is dependency-injected — it takes a ``comparer``
(``HEUnit`` → :class:`HECompareResult`) — so these tests exercise the full aggregation,
JSONL persistence and the clean-gold status folding WITHOUT the farchive or the geom lane.
No network, no pdfium.
"""
from __future__ import annotations

import json
from pathlib import Path

from lawvm.tools.fi_he_ir_compare import HECompareResult, OpDivergence
from lawvm.tools.fi_he_ir_corpus import (
    HEUnit,
    aggregate_rows,
    run_he_corpus,
)


def _compared(he_id: str, divergences=()) -> HECompareResult:
    return HECompareResult(
        he_id=he_id,
        branch_id="fi/he/2020/1",
        compare_status="compared",
        divergences=tuple(divergences),
        xml_op_count=3,
        pdf_op_count=3,
        payload_compared=1,
        payload_deferred=1,
    )


def _typed(he_id: str, status: str) -> HECompareResult:
    return HECompareResult(he_id, "fi/he/2020/1", status, (), 0, 0, "detail")


def _missing_div(ref: str) -> OpDivergence:
    return OpDivergence("op_missing_in_pdf", ref, f"replace {ref}", None, "dropped")


def _matched_div(ref: str) -> OpDivergence:
    return OpDivergence("matched", ref, f"replace {ref}", f"replace {ref}", "")


def test_driver_aggregates_and_persists(tmp_path: Path) -> None:
    units = [HEUnit(2020, i, f"HE {i}/2020 vp") for i in range(1, 5)]
    results = {
        "HE 1/2020 vp": _compared("HE 1/2020 vp", (_matched_div("a/1"),)),  # exact
        "HE 2/2020 vp": _compared("HE 2/2020 vp", (_missing_div("b/2"),)),  # 1 typed
        "HE 3/2020 vp": _typed("HE 3/2020 vp", "xml_wrapper_only"),
        "HE 4/2020 vp": _typed("HE 4/2020 vp", "new_statute_only"),
    }
    out = tmp_path / "he.jsonl"
    report = run_he_corpus(
        units, lambda u: results[u.he_id], out_path=str(out), worst_limit=5
    )
    assert report.n_attempted == 4
    assert report.n_compared == 2
    assert report.n_exact == 1
    assert report.exact_match_rate == 0.5
    assert report.total_typed_divergences == 1
    assert report.bucket_counts["op_missing_in_pdf"] == 1
    assert report.status_counts["xml_wrapper_only"] == 1
    assert report.status_counts["new_statute_only"] == 1
    assert report.payload_compared == 2 and report.payload_deferred == 2
    # Persistence: one JSONL row per unit.
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 4
    assert {r["compare_status"] for r in rows} == {
        "compared", "xml_wrapper_only", "new_statute_only",
    }


def test_comparer_exception_is_typed_error(tmp_path: Path) -> None:
    units = [HEUnit(2020, 1, "HE 1/2020 vp")]

    def boom(_u: HEUnit) -> HECompareResult:
        raise RuntimeError("bad pdf")

    report = run_he_corpus(units, boom)
    assert report.status_counts["error"] == 1
    assert report.n_compared == 0


def test_rank_worst_orders_by_typed_count() -> None:
    rows = []
    r1 = _compared("HE 1/2020 vp", (_missing_div("x/1"), _missing_div("x/2")))
    r2 = _compared("HE 2/2020 vp", (_missing_div("y/1"),))
    from lawvm.tools.fi_he_ir_corpus import _row_from_result

    report = aggregate_rows([_row_from_result(r1), _row_from_result(r2)], worst_limit=5)
    assert [w.he_id for w in report.worst] == ["HE 1/2020 vp", "HE 2/2020 vp"]
    assert report.worst[0].typed_divergence_count == 2
