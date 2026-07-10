"""Thread-safe token + throughput ledger for vision-model calls (OBSERVABILITY only).

Every local-vision model call funnels through ``vision_producer._post_chat`` (the
single HTTP choke point, which also holds the ``VISION_INFLIGHT_GATE`` semaphore
around the round-trip). The llama.cpp / OpenAI-compat response carries token
accounting (``usage.prompt_tokens`` / ``usage.completion_tokens``) and, on
llama.cpp, server-side throughput (``timings.prompt_per_second`` /
``timings.predicted_per_second``) that the parse path discards. This module is a
process-wide, THREAD-SAFE ledger that records one row per call — input/output
tokens, measured wall time, and the server's prompt/decode tok/s — tagged with a
per-call UNIT (pdf / page / lane) so the same rows roll up by any granularity
(per-page → per-PDF → per-run / corpus).

The mekanismirealismi LLM guide's token doctrine motivates the *why*: output
tokens are ~40× the cost of input on a decode-bound local server, so a corpus
harness needs a token + throughput column to see where the decode budget goes and
whether the GPU is saturated. ``summary()`` reports total input/output/total
tokens, wall seconds, **wall tok/s** (real throughput, idle included) and
**compute tok/s** (from the server timings — the busy-only rate) and their
**ratio** — a GPU-utilization / idle proxy (ratio → 1 means the GPU was saturated
across the wall clock; a low ratio means queueing / transport / idle dominated).

CONCURRENCY. Processing is now concurrent — a bounded per-page ``ThreadPool`` in
``page_level.build_page_simulacra`` plus the in-flight semaphore — so N worker
threads append rows at once. Accumulation is guarded by a single lock; N threads ×
M calls yields EXACTLY N·M rows with no lost updates.

THREAD-LOCAL UNIT TAGGING. ``contextvars`` do NOT propagate into
``ThreadPoolExecutor`` worker threads (a worker starts with a fresh, empty
context), so the unit is carried on a ``threading.local`` STACK instead. A caller
opens ``with meter_unit(pdf=..., page=..., lane=...)`` *inside* the code that runs
on the worker thread (e.g. the per-page worker body), and ``_post_chat`` — running
on that SAME thread — reads ``current_unit()``. A call made with no unit on the
stack is tagged ``unit=unattributed`` (never dropped).

DETERMINISM FIREWALL. The meter is observability ONLY. It appends to a side list;
it NEVER touches the value ``_post_chat`` returns, and the pipeline assembles
strictly by index (temperature=0), so the parse result is byte-identical whether
or not the meter is present. ``observe`` is defensive (a malformed response yields
a typed PARTIAL row, never a raise), and the ``_post_chat`` call site additionally
guards it, so a meter fault can never perturb a parse.
"""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Mapping, Optional, Tuple, cast

# A frozen, order-normalized set of unit tags for one row (hashable + immutable).
UnitTags = Tuple[Tuple[str, str], ...]

# The tag a call with no unit on the stack gets — attributed, never dropped.
UNATTRIBUTED: UnitTags = (("unit", "unattributed"),)


# --------------------------------------------------------------------------- #
# Thread-local unit stack (crosses a ThreadPool ONLY because it is set on the   #
# worker thread; contextvars do not — see module docstring).                    #
# --------------------------------------------------------------------------- #

_LOCAL = threading.local()


def _stack() -> List[Dict[str, str]]:
    stack = getattr(_LOCAL, "unit_stack", None)
    if stack is None:
        stack = []
        _LOCAL.unit_stack = stack
    return stack


@contextmanager
def meter_unit(**tags: object) -> Iterator[None]:
    """Push a unit frame onto THIS thread's stack for the duration of the block.

    Keys are free-form (``pdf`` / ``page`` / ``lane`` / …); ``None`` values are
    dropped so an unset dimension never becomes a literal ``"None"`` tag. Frames
    nest — an inner frame's keys override an outer frame's for overlapping keys
    (later wins), so a per-PDF frame can set ``pdf`` and a per-page frame add
    ``page``. Must be entered on the SAME thread that will issue the model call
    (i.e. inside the ThreadPool worker body), since the stack is thread-local.
    """
    frame = {str(k): str(v) for k, v in tags.items() if v is not None}
    stack = _stack()
    stack.append(frame)
    try:
        yield
    finally:
        stack.pop()


def current_unit() -> UnitTags:
    """The merged, order-normalized unit tags for the calling thread right now.

    Returns ``UNATTRIBUTED`` when no frame is active — a call is always tagged.
    """
    merged: Dict[str, str] = {}
    for frame in _stack():
        merged.update(frame)
    if not merged:
        return UNATTRIBUTED
    return tuple(sorted(merged.items()))


# --------------------------------------------------------------------------- #
# Row + summary carriers.                                                       #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class TokenRow:
    """One recorded model call.

    ``input_tokens`` / ``output_tokens`` are ``None`` when the response carried no
    ``usage`` (``partial=True``); ``prompt_tps`` / ``decode_tps`` are ``None`` when
    it carried no llama.cpp ``timings``. ``wall_ms`` is always measured around the
    round-trip. ``unit_tags`` attributes the row to its pdf / page / lane.
    """

    input_tokens: Optional[int]
    output_tokens: Optional[int]
    wall_ms: float
    prompt_tps: Optional[float]
    decode_tps: Optional[float]
    unit_tags: UnitTags
    partial: bool = False

    @property
    def total_tokens(self) -> Optional[int]:
        if self.input_tokens is None or self.output_tokens is None:
            return None
        return self.input_tokens + self.output_tokens

    def compute_seconds(self) -> Optional[float]:
        """Server-side busy time for this call, derived from the timings.

        ``prompt_tokens / prompt_per_second + completion_tokens /
        predicted_per_second`` — the time the GPU actually spent on this request
        (prefill + decode). ``None`` unless BOTH token counts and BOTH positive
        per-second rates are present (an honest partial, never a guess).
        """
        if self.input_tokens is None or self.output_tokens is None:
            return None
        if not self.prompt_tps or not self.decode_tps:
            return None
        if self.prompt_tps <= 0 or self.decode_tps <= 0:
            return None
        return self.input_tokens / self.prompt_tps + self.output_tokens / self.decode_tps

    def tag_value(self, key: str) -> Optional[str]:
        for k, v in self.unit_tags:
            if k == key:
                return v
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "wall_ms": self.wall_ms,
            "prompt_tps": self.prompt_tps,
            "decode_tps": self.decode_tps,
            "unit_tags": {k: v for k, v in self.unit_tags},
            "partial": self.partial,
        }


@dataclass(frozen=True, slots=True)
class TokenSummary:
    """Rolled-up totals + throughput over a set of rows.

    ``wall_tok_per_s`` is real throughput (total tokens over summed wall seconds,
    idle included); ``compute_tok_per_s`` is the busy-only rate over the rows that
    carried server timings; ``throughput_ratio`` = wall÷compute ∈ (0, 1] is the
    GPU-utilization / idle proxy (→1 saturated, low = queueing / transport idle).
    ``None`` for a rate means no row supported it (no wall, or no timed rows).
    """

    calls: int
    partial_calls: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    wall_seconds: float
    compute_seconds: float
    timed_calls: int
    wall_tok_per_s: Optional[float]
    compute_tok_per_s: Optional[float]
    throughput_ratio: Optional[float]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "calls": self.calls,
            "partial_calls": self.partial_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "wall_seconds": self.wall_seconds,
            "compute_seconds": self.compute_seconds,
            "timed_calls": self.timed_calls,
            "wall_tok_per_s": self.wall_tok_per_s,
            "compute_tok_per_s": self.compute_tok_per_s,
            "throughput_ratio": self.throughput_ratio,
        }


def summarize(rows: Tuple[TokenRow, ...]) -> TokenSummary:
    """Roll a tuple of rows up into a :class:`TokenSummary` (pure, no lock)."""
    input_tokens = 0
    output_tokens = 0
    partial_calls = 0
    wall_seconds = 0.0
    compute_seconds = 0.0
    timed_calls = 0
    compute_tokens = 0
    for row in rows:
        wall_seconds += row.wall_ms / 1000.0
        if row.input_tokens is not None:
            input_tokens += row.input_tokens
        if row.output_tokens is not None:
            output_tokens += row.output_tokens
        if row.partial:
            partial_calls += 1
        cs = row.compute_seconds()
        if cs is not None and cs > 0:
            compute_seconds += cs
            timed_calls += 1
            # row.total_tokens is not-None here (compute_seconds guards it).
            compute_tokens += row.total_tokens or 0
    total_tokens = input_tokens + output_tokens
    wall_tps = (total_tokens / wall_seconds) if wall_seconds > 0 else None
    compute_tps = (compute_tokens / compute_seconds) if compute_seconds > 0 else None
    ratio: Optional[float]
    if wall_tps is not None and compute_tps is not None and compute_tps > 0:
        ratio = wall_tps / compute_tps
    else:
        ratio = None
    return TokenSummary(
        calls=len(rows),
        partial_calls=partial_calls,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        wall_seconds=wall_seconds,
        compute_seconds=compute_seconds,
        timed_calls=timed_calls,
        wall_tok_per_s=wall_tps,
        compute_tok_per_s=compute_tps,
        throughput_ratio=ratio,
    )


# --------------------------------------------------------------------------- #
# Response parsing → a row (defensive: a malformed response is a partial row).   #
# --------------------------------------------------------------------------- #


def _coerce_int(value: object) -> Optional[int]:
    if isinstance(value, bool):  # bool is an int subclass — reject explicitly.
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def _coerce_float(value: object) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def row_from_response(
    response: object, wall_ms: float, unit_tags: UnitTags
) -> TokenRow:
    """Build a :class:`TokenRow` from a chat-completions response JSON.

    Sources input/output from ``usage.prompt_tokens`` / ``usage.completion_tokens``
    and, when present, prompt/decode tok/s from llama.cpp ``timings``
    (``prompt_per_second`` → ``prompt_tps``, ``predicted_per_second`` →
    ``decode_tps``). A missing / malformed ``usage`` degrades to a typed PARTIAL
    row (tokens ``None``, ``partial=True``) — the wall time is still recorded.
    Never raises.
    """
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    prompt_tps: Optional[float] = None
    decode_tps: Optional[float] = None

    body = cast("Mapping[str, Any]", response) if isinstance(response, Mapping) else None
    usage = body.get("usage") if body is not None else None
    if isinstance(usage, Mapping):
        usage_m = cast("Mapping[str, Any]", usage)
        input_tokens = _coerce_int(usage_m.get("prompt_tokens"))
        output_tokens = _coerce_int(usage_m.get("completion_tokens"))

    timings = body.get("timings") if body is not None else None
    if isinstance(timings, Mapping):
        timings_m = cast("Mapping[str, Any]", timings)
        prompt_tps = _coerce_float(timings_m.get("prompt_per_second"))
        decode_tps = _coerce_float(timings_m.get("predicted_per_second"))

    partial = input_tokens is None or output_tokens is None
    return TokenRow(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        wall_ms=float(wall_ms),
        prompt_tps=prompt_tps,
        decode_tps=decode_tps,
        unit_tags=unit_tags,
        partial=partial,
    )


# --------------------------------------------------------------------------- #
# The ledger.                                                                   #
# --------------------------------------------------------------------------- #


class TokenMeter:
    """A process-wide, thread-safe append-only ledger of model-call rows.

    ``observe`` is the one-line hook the choke point (``_post_chat``) calls with the
    parsed response JSON and the measured wall time; it tags the row from the
    calling thread's unit stack, appends under the lock, and returns the row.
    ``summary`` / ``rollup`` / ``snapshot`` read a consistent copy; ``reset`` clears
    (returning the pre-clear snapshot) so a harness can meter per PDF / per sweep
    point.
    """

    __slots__ = ("_lock", "_rows")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: List[TokenRow] = []

    def record(self, row: TokenRow) -> TokenRow:
        with self._lock:
            self._rows.append(row)
        return row

    def observe(
        self,
        response: object,
        wall_ms: float,
        *,
        unit_tags: Optional[UnitTags] = None,
    ) -> TokenRow:
        """Parse ``response`` → row, tag it (thread-local unit by default), append.

        Defensive: any parse hiccup yields a PARTIAL row rather than raising, so an
        observability call can never perturb the metered pipeline.
        """
        tags = unit_tags if unit_tags is not None else current_unit()
        try:
            row = row_from_response(response, wall_ms, tags)
        except Exception:  # observability must never raise into the parse path.
            row = TokenRow(None, None, float(wall_ms), None, None, tags, partial=True)
        return self.record(row)

    def rows(self) -> Tuple[TokenRow, ...]:
        with self._lock:
            return tuple(self._rows)

    def summary(self) -> TokenSummary:
        return summarize(self.rows())

    def rollup(self, tag_key: str) -> Dict[str, TokenSummary]:
        """Group rows by the value of ``tag_key`` → per-group summary.

        Rows lacking the key fall under ``"unattributed"``. This is the one primitive
        behind per-page (``rollup("page")``), per-PDF (``rollup("pdf")``), and
        per-lane (``rollup("lane")``) reports; the run/corpus total is ``summary()``.
        """
        groups: Dict[str, List[TokenRow]] = {}
        for row in self.rows():
            key = row.tag_value(tag_key) or "unattributed"
            groups.setdefault(key, []).append(row)
        return {k: summarize(tuple(v)) for k, v in groups.items()}

    def snapshot(self) -> "MeterSnapshot":
        """A consistent, immutable view of the ledger (rows + roll-up summary)."""
        rows = self.rows()
        return MeterSnapshot(rows=rows, summary=summarize(rows))

    def reset(self) -> "MeterSnapshot":
        """Atomically clear the ledger, returning the snapshot taken BEFORE clearing."""
        with self._lock:
            rows = tuple(self._rows)
            self._rows = []
        return MeterSnapshot(rows=rows, summary=summarize(rows))


@dataclass(frozen=True, slots=True)
class MeterSnapshot:
    """An immutable point-in-time view of a :class:`TokenMeter`."""

    rows: Tuple[TokenRow, ...]
    summary: TokenSummary

    def rollup(self, tag_key: str) -> Dict[str, TokenSummary]:
        groups: Dict[str, List[TokenRow]] = {}
        for row in self.rows:
            key = row.tag_value(tag_key) or "unattributed"
            groups.setdefault(key, []).append(row)
        return {k: summarize(tuple(v)) for k, v in groups.items()}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "summary": self.summary.to_dict(),
            "rows": [row.to_dict() for row in self.rows],
        }

    def to_json(self, *, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)


# The ONE process-wide ledger every ``VisionPageProducer`` (and any other caller
# that imports it) records into — module-level so nested per-PDF × per-page pools
# all accumulate into the SAME object (a per-instance meter would fragment the
# corpus-wide totals). Observability only; see the determinism-firewall note above.
METER = TokenMeter()


def snapshot() -> MeterSnapshot:
    """Snapshot the process-wide :data:`METER` (convenience for harnesses)."""
    return METER.snapshot()


def reset() -> MeterSnapshot:
    """Clear the process-wide :data:`METER`, returning the pre-clear snapshot."""
    return METER.reset()


def summary() -> TokenSummary:
    """Roll the process-wide :data:`METER` up into a :class:`TokenSummary`."""
    return METER.summary()
