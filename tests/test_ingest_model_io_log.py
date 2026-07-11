"""Hermetic tests for the durable model-I/O log (no backend, no network).

Asserts: inert unless ``LAWVM_MODEL_IO_LOG`` is set; append-only JSONL; images are
stored as {sha256, len} METADATA never the base64 blob; empty completions are still
recorded (the flaky-decoder signal); the calling thread's meter_unit tags ride along;
a malformed response never raises (pure guarded side channel)."""
from __future__ import annotations

import importlib
import json

from lawvm.ingest.llm_backends.token_meter import meter_unit


def _reload_with_path(monkeypatch, path):
    import lawvm.ingest.llm_backends.model_io_log as mio

    monkeypatch.setenv("LAWVM_MODEL_IO_LOG", str(path))
    return importlib.reload(mio)


_PAYLOAD = {
    "model": "unsloth/qwen",
    "max_tokens": 48,
    "temperature": 0.0,
    "messages": [
        {"role": "system", "content": "SYSTEM PROMPT"},
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJDRA=="}},
                {"type": "text", "text": "read this page"},
            ],
        },
    ],
}


def test_inert_without_env(monkeypatch, tmp_path):
    # importlib.import_module (not `import ... as mio`) so `mio`'s inferred type is the
    # general ModuleType — matching importlib.reload's return type on reassignment below.
    mio = importlib.import_module("lawvm.ingest.llm_backends.model_io_log")

    monkeypatch.delenv("LAWVM_MODEL_IO_LOG", raising=False)
    mio = importlib.reload(mio)
    assert not mio.enabled()
    # record is a no-op — must not raise and must write nothing.
    mio.record(_PAYLOAD, {"choices": [{"message": {"content": "x"}}]}, 10.0)


def test_records_content_and_image_metadata(monkeypatch, tmp_path):
    log = tmp_path / "mio.jsonl"
    mio = _reload_with_path(monkeypatch, log)
    assert mio.enabled()
    out = {
        "choices": [{"message": {"content": "1 HEADING 0 L1"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1200, "completion_tokens": 9},
    }
    with meter_unit(pdf="doc.pdf", page="4", lane="span"):
        mio.record(_PAYLOAD, out, 123.4)
    rows = [json.loads(ln) for ln in log.read_text().splitlines()]
    assert len(rows) == 1
    r = rows[0]
    assert r["unit"] == {"pdf": "doc.pdf", "page": "4", "lane": "span"}
    assert r["completion"] == "1 HEADING 0 L1" and r["completion_len"] == 14
    assert r["input_tokens"] == 1200 and r["output_tokens"] == 9
    img = r["messages"][1]["content"][0]["image"]
    # image is metadata (digest + length), never the base64 blob.
    assert set(img) == {"sha256", "b64_len"} and img["b64_len"] == len("QUJDRA==")
    assert "QUJDRA==" not in log.read_text()


def test_empty_completion_is_recorded(monkeypatch, tmp_path):
    """The flaky decoder returns empty completions — the log must NOT hide them."""
    log = tmp_path / "mio.jsonl"
    mio = _reload_with_path(monkeypatch, log)
    out = {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}],
           "usage": {"prompt_tokens": 1200, "completion_tokens": 0}}
    mio.record(_PAYLOAD, out, 88.0)
    r = json.loads(log.read_text().splitlines()[0])
    assert r["completion"] == "" and r["completion_len"] == 0 and r["output_tokens"] == 0


def test_append_only_and_malformed_never_raises(monkeypatch, tmp_path):
    log = tmp_path / "mio.jsonl"
    mio = _reload_with_path(monkeypatch, log)
    mio.record(_PAYLOAD, {"choices": [{"message": {"content": "a"}}]}, 1.0)
    mio.record(_PAYLOAD, {"garbage": True}, 2.0)  # malformed → guarded, still a row
    mio.record(_PAYLOAD, None, 3.0)  # None response → guarded
    rows = log.read_text().splitlines()
    assert len(rows) == 3
    assert json.loads(rows[1])["completion"] == ""  # malformed degrades, never raises
