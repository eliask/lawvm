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


def test_corroborate_edge_folds_receipts_and_stays_byte_identical_offline(
    tmp_path: Path,
) -> None:
    """The CORROBORATE edge: an injected witness folds receipts; the free lane is unchanged.

    Hermetic — no farchive, no vision backend. A ``garble_suspect`` (escalation-pending)
    row carrying a verdict-changed receipt (a caught false-exact) folds into
    ``n_escalation_resolved`` / ``n_verdict_changed`` and emits the receipt into its JSONL
    row; a receipt-less (offline) row's JSONL is byte-identical to the pre-corroboration
    shape (no ``escalation_pending`` / ``corroboration_receipt`` keys).
    """
    from lawvm.ingest.corroboration import (
        CorroborationReceipt,
        EscalationKind,
        EscalationPending,
    )
    from lawvm.tools.fi_he_ir_corpus import _row_from_result

    # Offline garble_suspect: no receipt attached (the honest un-resolved state).
    offline = _typed("HE 1/2020 vp", "garble_suspect")
    # Online garble_suspect: a witness caught a false-exact (verdict_changed).
    pending = EscalationPending(
        unit_id="HE 2/2020 vp",
        kind=EscalationKind.GARBLE_READ,
        reason="garbled text layer",
        region="akn/fi/.../main.pdf",
        candidate_text="garbled",
    )
    receipt = CorroborationReceipt(
        unit_id="HE 2/2020 vp",
        kind=EscalationKind.GARBLE_READ,
        candidate="garbled",
        vision_read="the clean amended section",
        agreed=False,
        verdict_changed=True,
        region="akn/fi/.../main.pdf",
        witness_fingerprint="deadbeefcafef00d",
    )
    online = HECompareResult(
        "HE 2/2020 vp", "fi/he/2020/2", "garble_suspect", (), 0, 0, "garbled",
        escalation_pending=pending, corroboration_receipt=receipt,
    )

    out = tmp_path / "he.jsonl"
    rows = [_row_from_result(offline), _row_from_result(online)]
    by_id = {"HE 1/2020 vp": offline, "HE 2/2020 vp": online}
    report = run_he_corpus(
        [HEUnit(2020, 1, "HE 1/2020 vp"), HEUnit(2020, 2, "HE 2/2020 vp")],
        lambda u: by_id[u.he_id],
        out_path=str(out),
    )
    # Both stay escalation-pending (a receipt RECORDS; it never flips the status to compared).
    assert report.n_escalation_pending == 2
    assert report.n_compared == 0
    # Receipt statistics folded from the online row only.
    assert report.n_escalation_resolved == 1
    assert report.n_verdict_changed == 1
    assert report.n_agreed == 0
    assert len(report.receipts) == 1 and report.receipts[0].verdict_changed

    persisted = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    # The OFFLINE row is byte-identical to the pre-corroboration shape: no new keys.
    assert "escalation_pending" not in persisted[0]
    assert "corroboration_receipt" not in persisted[0]
    # The ONLINE row carries the typed pending + the receipt.
    assert persisted[1]["escalation_pending"]["kind"] == "garble_read"
    assert persisted[1]["corroboration_receipt"]["verdict_changed"] is True
    assert persisted[1]["corroboration_receipt"]["witness_fingerprint"] == "deadbeefcafef00d"
    # aggregate_rows over the same rows agrees (single authority).
    assert aggregate_rows(rows).n_verdict_changed == 1


def test_comparer_exception_is_typed_error(tmp_path: Path) -> None:
    units = [HEUnit(2020, 1, "HE 1/2020 vp")]

    def boom(_u: HEUnit) -> HECompareResult:
        raise RuntimeError("bad pdf")

    report = run_he_corpus(units, boom)
    assert report.status_counts["error"] == 1
    assert report.n_compared == 0


def test_pdf_oversize_is_typed_non_compared_not_a_stall() -> None:
    # A pathological giant HE PDF must be TYPED (pdf_oversize) and folded as a benign
    # non-compared stratum — not stall the sweep, not enter the exact-match denominator.
    from lawvm.tools.fi_he_ir_compare import _NON_COMPARED_STATUSES
    from lawvm.tools.fi_he_ir_corpus import _row_from_result

    assert "pdf_oversize" in _NON_COMPARED_STATUSES
    rows = [
        _row_from_result(_compared("HE 1/2020 vp")),
        _row_from_result(_typed("HE 2/2020 vp", "pdf_oversize")),
    ]
    report = aggregate_rows(rows)
    assert report.n_compared == 1  # the oversize HE is NOT in the compared denominator
    assert report.status_counts.get("pdf_oversize") == 1


def test_rank_worst_orders_by_typed_count() -> None:
    rows = []
    r1 = _compared("HE 1/2020 vp", (_missing_div("x/1"), _missing_div("x/2")))
    r2 = _compared("HE 2/2020 vp", (_missing_div("y/1"),))
    from lawvm.tools.fi_he_ir_corpus import _row_from_result

    report = aggregate_rows([_row_from_result(r1), _row_from_result(r2)], worst_limit=5)
    assert [w.he_id for w in report.worst] == ["HE 1/2020 vp", "HE 2/2020 vp"]
    assert report.worst[0].typed_divergence_count == 2


# --------------------------------------------------------------------------- #
# LLM johtolause opt-in wiring (make_comparer / build_llm_johtolause_classify_fn) #
# --------------------------------------------------------------------------- #


def test_make_comparer_default_threads_no_classify_fn(monkeypatch) -> None:
    # Default (llm_johtolause=False) → classify_fn=None reaches compare_he_from_farchive,
    # so the mechanical lane runs and no LLM is constructed.
    import lawvm.tools.fi_he_ir_corpus as corpus

    seen: dict = {}

    def fake_compare(
        farchive, yr, num, *, he_id, max_pages, classify_fn,
        vision_reader=None, witness_prompt="", witness_model="",
    ):
        seen["classify_fn"] = classify_fn
        seen["vision_reader"] = vision_reader
        return HECompareResult(he_id, f"fi/he/{yr}/{num}", "not_applicable", (), 0, 0, "")

    monkeypatch.setattr(corpus, "compare_he_from_farchive", fake_compare)
    comparer = corpus.make_comparer("x.farchive")
    comparer(HEUnit(2020, 1, "HE 1/2020 vp"))
    assert seen["classify_fn"] is None


def test_make_comparer_llm_threads_the_built_classify_fn(monkeypatch) -> None:
    # llm_johtolause=True → the built classify_fn is threaded into every comparison.
    import lawvm.tools.fi_he_ir_corpus as corpus

    sentinel = object()
    monkeypatch.setattr(
        corpus, "build_llm_johtolause_classify_fn",
        lambda **kw: (sentinel, lambda: None),
    )
    seen: dict = {}

    def fake_compare(
        farchive, yr, num, *, he_id, max_pages, classify_fn,
        vision_reader=None, witness_prompt="", witness_model="",
    ):
        seen["classify_fn"] = classify_fn
        seen["vision_reader"] = vision_reader
        return HECompareResult(he_id, f"fi/he/{yr}/{num}", "not_applicable", (), 0, 0, "")

    monkeypatch.setattr(corpus, "compare_he_from_farchive", fake_compare)
    comparer = corpus.make_comparer("x.farchive", llm_johtolause=True)
    comparer(HEUnit(2020, 1, "HE 1/2020 vp"))
    assert seen["classify_fn"] is sentinel


def test_build_llm_johtolause_classify_fn_wires_cache_and_transport(
    tmp_path, monkeypatch
) -> None:
    # The real builder: a fake adjudicator (NO network) proves the chat_fn calls _chat with
    # region_locator='johtolause_tag', the tagger_id folds the resolved model, and the tag
    # is cached content-addressed in the given store path.
    from lawvm.finland.he_johtolause_tagger import JohtolauseTag

    calls: list = []

    class _FakeAdjudicator:
        def __init__(self, **kw) -> None:
            self.kw = kw

        def _resolve_model(self) -> str:
            return "qwen-test"

        def _chat(self, system: str, user: str, *, region_locator: str) -> str:
            calls.append(region_locator)
            return "JOHTOLAUSE"

    monkeypatch.setattr(
        "lawvm.ingest.llm_backends.llm_adjudicator.LlmWorkflowAdjudicator",
        _FakeAdjudicator,
    )
    from lawvm.tools.fi_he_ir_corpus import build_llm_johtolause_classify_fn

    store_path = str(tmp_path / "tags.farchive")
    classify_fn, close = build_llm_johtolause_classify_fn(store_path=store_path)
    try:
        tag = classify_fn("muutetaan lain (320/2017) 1 § ... seuraavasti:")
        assert tag is JohtolauseTag.JOHTOLAUSE
        assert calls == ["johtolause_tag"]
        # Re-classify the SAME window → cache HIT, no second transport call.
        classify_fn("muutetaan lain (320/2017) 1 § ... seuraavasti:")
        assert calls == ["johtolause_tag"]
    finally:
        close()
