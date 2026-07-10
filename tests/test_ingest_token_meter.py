"""Thread-safe token + throughput ledger (Task: token_meter, hermetic).

No network, no model, no real PDF lib. A fake ``urlopen`` returns a llama.cpp /
OpenAI-compat response carrying ``usage`` + ``timings``; the REAL
``vision_producer._post_chat`` runs against it so the instrumentation at the single
choke point is exercised end-to-end. Covers:

  * a call records the right input/output tokens + prompt/decode tok/s;
  * concurrent calls from many threads accumulate with NO lost updates
    (N threads × M calls → exactly N·M rows and exact token totals);
  * thread-local ``meter_unit`` tags attribute rows to the right pdf/page and roll
    up correctly across a ThreadPool (contextvars would NOT survive the hop);
  * a missing ``usage`` / ``timings`` degrades to a typed PARTIAL row (no crash);
  * ``summary()`` computes wall tok/s, compute tok/s, and their ratio;
  * the meter is a pure side channel — ``_post_chat`` returns identical content.
"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor

from lawvm.ingest.llm_backends import token_meter
from lawvm.ingest.llm_backends.token_meter import (
    TokenMeter,
    TokenRow,
    current_unit,
    meter_unit,
    row_from_response,
    summarize,
)
from lawvm.ingest.llm_backends.vision_producer import VisionPageProducer


# --------------------------------------------------------------------------- #
# Fakes: a response body + a urlopen that returns it (no network).             #
# --------------------------------------------------------------------------- #


def _response(
    *,
    content: str = "1 HEADING 0 L1\x1f",
    prompt_tokens: int | None = 1000,
    completion_tokens: int | None = 40,
    prompt_tps: float | None = 500.0,
    decode_tps: float | None = 25.0,
    finish_reason: str = "stop",
) -> dict:
    out: dict = {
        "choices": [
            {"message": {"content": content}, "finish_reason": finish_reason}
        ],
    }
    if prompt_tokens is not None or completion_tokens is not None:
        out["usage"] = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        }
    if prompt_tps is not None or decode_tps is not None:
        out["timings"] = {
            "prompt_per_second": prompt_tps,
            "predicted_per_second": decode_tps,
        }
    return out


class _FakeResp:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


def _install_fake_urlopen(monkeypatch, response: dict) -> None:
    body = json.dumps(response).encode("utf-8")

    def _fake_urlopen(req, timeout=None):  # noqa: ANN001 - test shim
        return _FakeResp(body)

    import lawvm.ingest.llm_backends.vision_producer as vp

    monkeypatch.setattr(vp.urllib.request, "urlopen", _fake_urlopen)


def _payload() -> dict:
    return {"model": "fake", "messages": [], "max_tokens": 64}


# --------------------------------------------------------------------------- #
# (1) One call records the right tokens + tps through the real choke point.    #
# --------------------------------------------------------------------------- #


def test_post_chat_records_tokens_and_tps(monkeypatch) -> None:
    token_meter.reset()
    _install_fake_urlopen(monkeypatch, _response())
    producer = VisionPageProducer(base_url="http://unused")

    content = producer._post_chat(_payload(), page_num=1)
    # The parse result is untouched by the meter (determinism firewall).
    assert content == "1 HEADING 0 L1\x1f"

    rows = token_meter.METER.rows()
    assert len(rows) == 1
    row = rows[0]
    assert row.input_tokens == 1000
    assert row.output_tokens == 40
    assert row.prompt_tps == 500.0
    assert row.decode_tps == 25.0
    assert row.partial is False
    assert row.wall_ms >= 0.0
    # No unit on the stack → attributed, never dropped.
    assert row.unit_tags == (("unit", "unattributed"),)


# --------------------------------------------------------------------------- #
# (2) Concurrency: N threads × M calls → exactly N·M rows, exact totals.       #
# --------------------------------------------------------------------------- #


def test_concurrent_calls_accumulate_without_loss(monkeypatch) -> None:
    token_meter.reset()
    _install_fake_urlopen(monkeypatch, _response(prompt_tokens=7, completion_tokens=3))
    producer = VisionPageProducer(base_url="http://unused")

    n_threads, m_calls = 12, 25

    def _worker() -> None:
        for _ in range(m_calls):
            producer._post_chat(_payload(), page_num=1)

    threads = [threading.Thread(target=_worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    summary = token_meter.METER.summary()
    assert summary.calls == n_threads * m_calls
    assert summary.input_tokens == 7 * n_threads * m_calls
    assert summary.output_tokens == 3 * n_threads * m_calls
    assert summary.partial_calls == 0


# --------------------------------------------------------------------------- #
# (3) Thread-local unit tagging survives a real ThreadPool + rolls up.         #
# --------------------------------------------------------------------------- #


def test_meter_unit_tags_across_threadpool_and_rollup(monkeypatch) -> None:
    token_meter.reset()
    _install_fake_urlopen(monkeypatch, _response(prompt_tokens=100, completion_tokens=10))
    producer = VisionPageProducer(base_url="http://unused")

    # Two PDFs, three pages each — the tag is set INSIDE the worker (the thread that
    # runs the call), which is exactly why a thread-local carries it where a
    # contextvar set in the submitting thread would not.
    jobs = [(pdf, page) for pdf in ("a.pdf", "b.pdf") for page in (1, 2, 3)]

    def _worker(job) -> None:  # noqa: ANN001 - test shim
        pdf, page = job
        with meter_unit(pdf=pdf, page=page):
            producer._post_chat(_payload(), page_num=page)

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(_worker, jobs))

    by_pdf = token_meter.METER.rollup("pdf")
    assert set(by_pdf) == {"a.pdf", "b.pdf"}
    assert by_pdf["a.pdf"].calls == 3
    assert by_pdf["a.pdf"].input_tokens == 300  # 3 pages × 100
    assert by_pdf["b.pdf"].output_tokens == 30

    by_page = token_meter.METER.rollup("page")
    assert set(by_page) == {"1", "2", "3"}
    # Each page number appears once per PDF → 2 calls.
    assert by_page["1"].calls == 2
    assert by_page["2"].input_tokens == 200


def test_current_unit_nesting_and_default() -> None:
    assert current_unit() == (("unit", "unattributed"),)
    with meter_unit(pdf="x.pdf"):
        assert current_unit() == (("pdf", "x.pdf"),)
        with meter_unit(page=4, pdf="override.pdf"):
            # Inner frame overrides overlapping keys; order-normalized.
            assert current_unit() == (("page", "4"), ("pdf", "override.pdf"))
        assert current_unit() == (("pdf", "x.pdf"),)
    assert current_unit() == (("unit", "unattributed"),)


def test_meter_unit_drops_none_valued_tags() -> None:
    with meter_unit(pdf="x.pdf", page=None, lane=None):
        assert current_unit() == (("pdf", "x.pdf"),)


# --------------------------------------------------------------------------- #
# (4) Missing usage / timings → typed PARTIAL row, no crash.                   #
# --------------------------------------------------------------------------- #


def test_missing_usage_degrades_to_partial_row(monkeypatch) -> None:
    token_meter.reset()
    resp = _response(prompt_tokens=None, completion_tokens=None, prompt_tps=None, decode_tps=None)
    _install_fake_urlopen(monkeypatch, resp)
    producer = VisionPageProducer(base_url="http://unused")

    content = producer._post_chat(_payload(), page_num=1)
    assert content == "1 HEADING 0 L1\x1f"  # parse still works

    rows = token_meter.METER.rows()
    assert len(rows) == 1
    row = rows[0]
    assert row.input_tokens is None
    assert row.output_tokens is None
    assert row.prompt_tps is None
    assert row.decode_tps is None
    assert row.partial is True
    assert row.wall_ms >= 0.0  # wall still measured


def test_row_from_response_defensive_on_garbage() -> None:
    # Not a mapping at all → partial row, never a raise.
    row = row_from_response("not-a-dict", 12.0, (("unit", "unattributed"),))
    assert row.partial is True
    assert row.input_tokens is None
    assert row.wall_ms == 12.0


# --------------------------------------------------------------------------- #
# (5) summary(): wall tok/s, compute tok/s, and their ratio.                   #
# --------------------------------------------------------------------------- #


def test_summary_wall_vs_compute_tok_per_s_and_ratio() -> None:
    meter = TokenMeter()
    # One fully-timed row: 1000 prompt @ 500 tok/s = 2.0s prefill; 40 decode @ 25
    # tok/s = 1.6s decode → compute = 3.6s over 1040 tokens. Wall = 8.0s (4.4s idle).
    meter.record(
        TokenRow(
            input_tokens=1000,
            output_tokens=40,
            wall_ms=8000.0,
            prompt_tps=500.0,
            decode_tps=25.0,
            unit_tags=(("unit", "unattributed"),),
        )
    )
    s = meter.summary()
    assert s.calls == 1
    assert s.total_tokens == 1040
    assert s.wall_seconds == 8.0
    assert abs(s.compute_seconds - 3.6) < 1e-9
    assert s.timed_calls == 1
    assert s.wall_tok_per_s is not None
    assert s.compute_tok_per_s is not None
    assert s.throughput_ratio is not None
    assert abs(s.wall_tok_per_s - 1040 / 8.0) < 1e-9
    assert abs(s.compute_tok_per_s - 1040 / 3.6) < 1e-9
    # ratio = wall / compute = compute_seconds / wall_seconds = 3.6 / 8.0 = 0.45.
    assert abs(s.throughput_ratio - 0.45) < 1e-9
    assert 0.0 < s.throughput_ratio <= 1.0


def test_summary_partial_rows_excluded_from_compute_but_counted_for_wall() -> None:
    meter = TokenMeter()
    # A timed row + a partial row (no usage/timings): the partial contributes wall
    # only, never compute; wall tok/s reflects idle, compute the busy rate.
    meter.record(
        TokenRow(500, 20, 4000.0, 250.0, 20.0, (("unit", "unattributed"),))
    )
    meter.record(
        TokenRow(None, None, 4000.0, None, None, (("unit", "unattributed"),), partial=True)
    )
    s = meter.summary()
    assert s.calls == 2
    assert s.partial_calls == 1
    assert s.timed_calls == 1
    assert s.total_tokens == 520
    assert s.wall_seconds == 8.0
    # compute = 500/250 + 20/20 = 2.0 + 1.0 = 3.0s over the ONE timed row's 520 tok.
    assert abs(s.compute_seconds - 3.0) < 1e-9
    assert s.compute_tok_per_s is not None
    assert s.wall_tok_per_s is not None
    assert abs(s.compute_tok_per_s - 520 / 3.0) < 1e-9
    assert abs(s.wall_tok_per_s - 520 / 8.0) < 1e-9


def test_summary_empty_is_zeroed_not_nan() -> None:
    s = summarize(())
    assert s.calls == 0
    assert s.total_tokens == 0
    assert s.wall_tok_per_s is None
    assert s.compute_tok_per_s is None
    assert s.throughput_ratio is None


# --------------------------------------------------------------------------- #
# snapshot() / reset() + JSON dump public API.                                 #
# --------------------------------------------------------------------------- #


def test_snapshot_reset_and_json_dump() -> None:
    meter = TokenMeter()
    meter.record(TokenRow(10, 2, 100.0, 100.0, 10.0, (("pdf", "z.pdf"), ("page", "1"))))
    snap = meter.snapshot()
    assert snap.summary.calls == 1
    dumped = json.loads(snap.to_json())
    assert dumped["summary"]["total_tokens"] == 12
    assert dumped["rows"][0]["unit_tags"] == {"pdf": "z.pdf", "page": "1"}

    pre = meter.reset()
    assert pre.summary.calls == 1  # returns the pre-clear view
    assert meter.summary().calls == 0  # cleared
