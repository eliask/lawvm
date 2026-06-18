"""H2 — deterministic parallel corpus sweep harness.

Unifies the worker-pool + ordered-reassembly pattern shared by:
  - scripts/audit_*.py (ProcessPoolExecutor over statute IDs)
  - export_fi_* via _parallel_corpus.project_corpus_parallel
"""
from __future__ import annotations

import os
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, TypeVar

T = TypeVar("T")

MAX_WORKERS = 8


@dataclass(frozen=True, slots=True)
class SweepResult:
    statute_ids: tuple[str, ...]
    rows: tuple[Any, ...]
    errors: tuple[tuple[str, str], ...]
    wall_seconds: float


def _auto_workers(requested: int, item_count: int) -> int:
    if requested == 1:
        return 1
    if requested > 0:
        return min(requested, MAX_WORKERS, max(item_count, 1))
    cpu = os.cpu_count() or 4
    return min(MAX_WORKERS, max(1, cpu // 2), max(item_count, 1))


def sweep_corpus_ordered(
    statute_ids: Sequence[str],
    worker: Callable[[str], T],
    *,
    workers: int = 0,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> SweepResult[T]:
    """Map ``worker`` over statute IDs; preserve input order in results."""
    ids = tuple(statute_ids)
    if not ids:
        return SweepResult(statute_ids=(), rows=(), errors=(), wall_seconds=0.0)

    started = time.time()
    pool_size = _auto_workers(workers, len(ids))

    if pool_size == 1:
        rows: list[T | None] = []
        errors: list[tuple[str, str]] = []
        for index, sid in enumerate(ids, start=1):
            if on_progress:
                on_progress(index, len(ids), sid)
            try:
                rows.append(worker(sid))
            except Exception as exc:
                rows.append(None)
                errors.append((sid, str(exc)))
        typed_rows = tuple(row for row in rows if row is not None)
        return SweepResult(
            statute_ids=ids,
            rows=typed_rows,
            errors=tuple(errors),
            wall_seconds=time.time() - started,
        )

    indexed: dict[int, T] = {}
    errors: list[tuple[str, str]] = []
    with ProcessPoolExecutor(max_workers=pool_size) as pool:
        future_map = {pool.submit(worker, sid): (index, sid) for index, sid in enumerate(ids)}
        done = 0
        for future in as_completed(future_map):
            index, sid = future_map[future]
            done += 1
            if on_progress:
                on_progress(done, len(ids), sid)
            try:
                indexed[index] = future.result()
            except Exception as exc:
                errors.append((sid, str(exc)))

    ordered_rows = tuple(indexed[i] for i in range(len(ids)) if i in indexed)
    return SweepResult(
        statute_ids=ids,
        rows=ordered_rows,
        errors=tuple(errors),
        wall_seconds=time.time() - started,
    )
