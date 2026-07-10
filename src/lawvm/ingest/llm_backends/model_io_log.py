"""Durable model-I/O log — the full prompt+completion of every model call.

The token meter (``token_meter``) records COUNTS; this records CONTENT: for each
call the sanitized request (system + user text, images as METADATA — a digest and
byte length, NEVER the base64 blob) and the raw completion, plus token usage and the
``meter_unit`` tags (pdf / page / lane). Recomputing a vision read is expensive, so a
durable log makes every call auditable + replayable offline WITHOUT re-hitting the
GPU — and it captures the intermittently-EMPTY completions the flaky decoder emits,
which are exactly what a counts-only meter hides.

Append-only JSONL (one object per call), because a log wants EVERY call including
repeats (a flaky model returns different completions for the same input) — content
addressing would dedup away the signal. Thread-safe (a lock around the append; the
per-page / per-PDF pools all funnel through the one ``_post_chat`` choke point).

OPT-IN + INERT by default: enabled only when ``LAWVM_MODEL_IO_LOG`` names a path, so
tests / CI never write. A pure SIDE CHANNEL: every function is fully guarded so no
logging fault can perturb the returned content (determinism firewall) — a broken log
drops the record, never the call.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from typing import Any, Dict, List, Mapping, Optional, Sequence

from lawvm.ingest.llm_backends.token_meter import current_unit

_LOG_PATH: Optional[str] = os.environ.get("LAWVM_MODEL_IO_LOG") or None
_LOCK = threading.Lock()


def enabled() -> bool:
    """The log writes only when ``LAWVM_MODEL_IO_LOG`` names a path (else inert)."""
    return _LOG_PATH is not None


def _sanitize_content(content: Any) -> Any:
    """One message's ``content`` with any image blob replaced by {sha256,len} meta.

    A string content passes through; a multimodal list keeps text parts verbatim and
    collapses each ``image_url`` data URL to ``{"image": {"sha256", "b64_len"}}`` —
    the digest identifies the image (re-derivable from the PDF) without storing pixels.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, Sequence):
        return content
    out: List[Any] = []
    for part in content:
        if not isinstance(part, Mapping):
            out.append(part)
            continue
        if part.get("type") == "image_url":
            url = ""
            iu = part.get("image_url")
            if isinstance(iu, Mapping):
                url = str(iu.get("url", ""))
            b64 = url.split(",", 1)[1] if "," in url else url
            out.append(
                {
                    "image": {
                        "sha256": hashlib.sha256(b64.encode("utf-8")).hexdigest(),
                        "b64_len": len(b64),
                    }
                }
            )
        elif part.get("type") == "text":
            out.append({"text": part.get("text", "")})
        else:
            out.append(part)
    return out


def _sanitize_messages(messages: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not isinstance(messages, Sequence):
        return out
    for m in messages:
        if not isinstance(m, Mapping):
            continue
        out.append({"role": m.get("role", ""), "content": _sanitize_content(m.get("content"))})
    return out


def record(payload: Mapping[str, Any], out: Any, wall_ms: float) -> None:
    """Append one call record (request sans image blobs + completion + usage + tags).

    Fully guarded: any fault (disabled, malformed response, unwritable path) drops the
    record silently — the caller's content is never affected."""
    if _LOG_PATH is None:
        return
    try:
        completion = ""
        finish_reason = None
        input_tokens = output_tokens = None
        if isinstance(out, Mapping):
            choices = out.get("choices")
            if isinstance(choices, Sequence) and choices and isinstance(choices[0], Mapping):
                msg = choices[0].get("message")
                if isinstance(msg, Mapping):
                    completion = str(msg.get("content") or "")
                finish_reason = choices[0].get("finish_reason")
            usage = out.get("usage")
            if isinstance(usage, Mapping):
                input_tokens = usage.get("prompt_tokens")
                output_tokens = usage.get("completion_tokens")
        rec = {
            "unit": dict(current_unit()),
            "model": payload.get("model"),
            "params": {
                "max_tokens": payload.get("max_tokens"),
                "temperature": payload.get("temperature"),
            },
            "messages": _sanitize_messages(payload.get("messages")),
            "completion": completion,
            "completion_len": len(completion),
            "finish_reason": finish_reason,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "wall_ms": round(wall_ms, 1),
        }
        line = json.dumps(rec, ensure_ascii=False, sort_keys=True)
        with _LOCK:
            with open(_LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception:
        pass
