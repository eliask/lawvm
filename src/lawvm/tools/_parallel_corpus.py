"""Deterministic parallel map over a Finland statute / locator corpus.

The Finland crosslink + section-text exporters (export_fi_refs, export_fi_actors,
export_fi_pools, export_fi_preparatory_refs, export_fi_sections_text, and the
statute phase of export_fi_inline_citations) all share one shape:

    for (_, statute_id) in corpus:           # ~59,500 statutes
        rows, diag_rows = project_one(statute_id, store)
        all_rows.extend(rows)
        all_diag_rows.extend(diag_rows)

That loop is embarrassingly parallel per statute (each statute is projected
independently from a read-only corpus store), but it was single-threaded, and
the six of them dominate ``lawvm rebuild-indexes`` (each ~18-19 min serially).

DETERMINISM CONTRACT (non-negotiable)
-------------------------------------
The parallel output MUST be byte-identical to the serial output. The serial
loop emits rows in *corpus order* (the order of ``corpus``). pyarrow's parquet
writer is byte-deterministic for an identical row list, so byte-identity holds
iff this helper reassembles rows in exactly the original corpus order
regardless of worker count or completion scheduling.

This module guarantees that by:
  * tagging every shard with its original corpus slice index,
  * collecting shard results into a dict keyed by that index, and
  * concatenating shards back in ascending index order before returning.

Within a shard, statutes are processed in their original order, so the within-
shard row order is identical to the serial order too. The concatenation of
ordered shards in ascending shard order therefore reproduces the serial row
sequence exactly.

Workers build their own process-local corpus store (it cannot be pickled);
``get_corpus_store`` is ``lru_cache``d per process, so the store is constructed
once per worker, not once per statute.
"""
from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# A per-statute projector: (statute_id, store) -> (rows, diag_rows).
# Must be a module-level callable so it is picklable for the process pool.
StatuteProjector = Callable[[str, Any], Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]]

# Hard pool-size ceiling. Each worker holds a full corpus store, so this bounds
# resident memory under the WSL2 ceiling regardless of host core count or an
# over-large explicit --workers. Matches the core export path's cap.
MAX_WORKERS = 8


# ---------------------------------------------------------------------------
# Worker-side state (one corpus store per worker process)
# ---------------------------------------------------------------------------

_WORKER_STORE: Any = None
_WORKER_PROJECTOR: Optional[StatuteProjector] = None
_WORKER_PROJECTOR_REF: Optional[Tuple[str, str]] = None


def _default_finland_store_factory() -> Any:
    """Default per-worker store builder: the Finland corpus store.

    Kept as the default so existing Finland callers need not pass a store
    factory.  Workers call this once per process (the underlying
    ``get_corpus_store`` is ``lru_cache``d).
    """
    from lawvm.finland.corpus import get_corpus_store

    return get_corpus_store()


def _worker_init(
    projector_ref: Tuple[str, str],
    store_factory_ref: Optional[Tuple[str, str]] = None,
) -> None:
    """Process-pool initializer: build the per-worker store + resolve the projector.

    ``projector_ref`` is a ``(module, qualname)`` pair identifying the per-statute
    projection function. We import it inside the worker rather than pickling the
    function object so the parent stays decoupled from worker import cost.

    ``store_factory_ref`` is an optional ``(module, qualname)`` pair identifying a
    zero-argument callable that builds the read-only corpus store passed to the
    projector.  When omitted, the Finland corpus store is used (the original
    behaviour), so this generalisation is backward-compatible.  Other
    jurisdictions (e.g. UK, whose store is an open Farchive handle) supply their
    own factory so the harness stays jurisdiction-neutral.
    """
    global _WORKER_STORE, _WORKER_PROJECTOR, _WORKER_PROJECTOR_REF
    import importlib

    if store_factory_ref is None:
        _WORKER_STORE = _default_finland_store_factory()
    else:
        sf_mod, sf_qualname = store_factory_ref
        store_factory = getattr(importlib.import_module(sf_mod), sf_qualname)
        _WORKER_STORE = store_factory()
    mod_name, qualname = projector_ref
    mod = importlib.import_module(mod_name)
    _WORKER_PROJECTOR = getattr(mod, qualname)
    _WORKER_PROJECTOR_REF = projector_ref


def _worker_run_shard(
    task: Tuple[int, Sequence[str]],
) -> Tuple[int, List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Project one shard of statute IDs; return (shard_index, rows, diag_rows).

    Rows are accumulated in the shard's own statute order, so the serial row
    order is preserved within the shard.
    """
    shard_index, statute_ids = task
    assert _WORKER_PROJECTOR is not None, "worker projector not initialized"
    rows: List[Dict[str, Any]] = []
    diag_rows: List[Dict[str, Any]] = []
    for statute_id in statute_ids:
        r, d = _WORKER_PROJECTOR(statute_id, _WORKER_STORE)
        rows.extend(r)
        diag_rows.extend(d)
    return shard_index, rows, diag_rows


# ---------------------------------------------------------------------------
# Sharding
# ---------------------------------------------------------------------------


def _make_shards(statute_ids: Sequence[str], workers: int) -> List[Tuple[int, List[str]]]:
    """Split ``statute_ids`` into contiguous, order-preserving shards.

    Contiguous shards (not round-robin) keep each shard's statutes in the
    original order and make the ascending-index concatenation reproduce the
    serial order exactly. We aim for several shards per worker so that uneven
    per-statute cost still balances across workers, while keeping shards large
    enough to amortize pickling/dispatch overhead.
    """
    n = len(statute_ids)
    if n == 0:
        return []
    # Target ~8 shards per worker (balance load vs. dispatch overhead), but
    # never produce empty shards and never fewer than `workers` shards.
    target_shards = max(workers, min(n, workers * 8))
    # Ceil-division chunk size.
    chunk = (n + target_shards - 1) // target_shards
    shards: List[Tuple[int, List[str]]] = []
    idx = 0
    start = 0
    while start < n:
        shards.append((idx, list(statute_ids[start:start + chunk])))
        idx += 1
        start += chunk
    return shards


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def project_corpus_parallel(
    *,
    statute_ids: Sequence[str],
    projector_ref: Tuple[str, str],
    serial_projector: StatuteProjector,
    store: Any,
    workers: int,
    store_factory_ref: Optional[Tuple[str, str]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Project every statute, returning (rows, diag_rows) in corpus order.

    Args:
        statute_ids:      Statute IDs in the exact corpus order the serial loop
                          would iterate (byte-identity depends on this order).
        projector_ref:    (module, qualname) of the per-statute projector, used
                          by worker processes to import the function.
        serial_projector: The same projector as a direct callable, used for the
                          workers<=1 fast path (and as the authoritative
                          definition the ref must resolve to).
        store:            Corpus store for the serial path. Workers build their
                          own (via ``store_factory_ref`` or the Finland default).
        workers:          Number of worker processes. <=1 runs serially.
        store_factory_ref: Optional (module, qualname) of a zero-arg store factory
                          for worker processes. When omitted, workers build the
                          Finland corpus store (backward-compatible default).

    Returns:
        (rows, diag_rows) concatenated in corpus order — byte-identical to the
        serial loop's accumulation order.
    """
    if workers <= 0:
        workers = max(1, (os.cpu_count() or 2) - 2)
    # Cap the pool at MAX_WORKERS. Each worker builds its own full corpus store
    # (get_corpus_store, one per process), so pool size is a direct multiplier on
    # resident memory; an uncapped cpu_count-2 default (e.g. 14 on a 16-core box)
    # would hold 14 corpus stores at once and trip the WSL2 memory ceiling.
    workers = min(workers, MAX_WORKERS)

    n = len(statute_ids)

    # Serial fast path: identical to the original loop. Also the safe fallback
    # for tiny corpora where process startup would dominate.
    if workers <= 1 or n <= 1:
        rows: List[Dict[str, Any]] = []
        diag_rows: List[Dict[str, Any]] = []
        for statute_id in statute_ids:
            r, d = serial_projector(statute_id, store)
            rows.extend(r)
            diag_rows.extend(d)
        return rows, diag_rows

    from concurrent.futures import as_completed

    from lawvm.tools._worker_pool import managed_executor

    shards = _make_shards(statute_ids, workers)

    # Collect shard outputs keyed by shard index for stable reassembly.
    shard_rows: Dict[int, List[Dict[str, Any]]] = {}
    shard_diags: Dict[int, List[Dict[str, Any]]] = {}

    with managed_executor(
        workers,
        initializer=_worker_init,
        initargs=(projector_ref, store_factory_ref),
    ) as pool:
        futures = {pool.submit(_worker_run_shard, task): task[0] for task in shards}
        for future in as_completed(futures):
            shard_index, rows_part, diag_part = future.result()
            shard_rows[shard_index] = rows_part
            shard_diags[shard_index] = diag_part

    # Reassemble in ascending shard order == original corpus order.
    all_rows: List[Dict[str, Any]] = []
    all_diags: List[Dict[str, Any]] = []
    for shard_index, _ in shards:
        all_rows.extend(shard_rows[shard_index])
        all_diags.extend(shard_diags[shard_index])

    return all_rows, all_diags
