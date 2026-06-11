"""Determinism contract for the parallel corpus projection helper.

The Finland crosslink + section-text exporters parallelize per-statute
projection via ``project_corpus_parallel``. Byte-identity of the resulting
parquet/jsonl artifacts depends entirely on one invariant: the helper must
return rows in the *exact corpus order* regardless of worker count or shard
completion scheduling.

These tests pin that invariant hermetically (no farchive / corpus store needed)
by driving the helper with a deterministic, order-sensitive fake projector.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

import pytest

from lawvm.tools import _parallel_corpus
from lawvm.tools._parallel_corpus import project_corpus_parallel, _make_shards


# A module-level projector so it is picklable for the process pool. It emits a
# per-statute row whose value encodes the statute id, plus a variable number of
# rows so that shard boundaries cannot be masked by uniform row counts.
def _fake_projector(statute_id: str, store: Any) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    n = (int(statute_id) % 3) + 1  # 1..3 rows
    rows = [{"sid": statute_id, "k": i} for i in range(n)]
    diags = [{"sid": statute_id, "diag": True}]
    return rows, diags


# Register the fake projector on the module under a stable qualname so the
# worker init can import it by (module, qualname).
_parallel_corpus._fake_projector = _fake_projector  # type: ignore[attr-defined]  # ty:ignore[unresolved-attribute]


def _expected_serial(statute_ids: List[str]) -> Tuple[list, list]:
    rows: list = []
    diags: list = []
    for sid in statute_ids:
        r, d = _fake_projector(sid, None)
        rows.extend(r)
        diags.extend(d)
    return rows, diags


@pytest.mark.parametrize("workers", [2, 4, 8])
def test_parallel_matches_serial_order(workers: int) -> None:
    statute_ids = [str(i) for i in range(500)]
    ref = ("lawvm.tools._parallel_corpus", "_fake_projector")

    serial_rows, serial_diags = project_corpus_parallel(
        statute_ids=statute_ids,
        projector_ref=ref,
        serial_projector=_fake_projector,
        store=None,
        workers=1,
    )
    par_rows, par_diags = project_corpus_parallel(
        statute_ids=statute_ids,
        projector_ref=ref,
        serial_projector=_fake_projector,
        store=None,
        workers=workers,
    )

    expected_rows, expected_diags = _expected_serial(statute_ids)

    # Serial path reproduces the plain loop.
    assert serial_rows == expected_rows
    assert serial_diags == expected_diags
    # Parallel path is identical to serial — same objects, same ORDER.
    assert par_rows == serial_rows
    assert par_diags == serial_diags


def test_shards_are_contiguous_and_cover_corpus() -> None:
    ids = [str(i) for i in range(97)]
    shards = _make_shards(ids, workers=4)
    # Indices are 0..k ascending and contiguous.
    assert [s[0] for s in shards] == list(range(len(shards)))
    # Concatenation in ascending shard order reproduces the input order exactly.
    flat: List[str] = []
    for _, chunk in shards:
        flat.extend(chunk)
    assert flat == ids
    # No empty shards.
    assert all(len(chunk) > 0 for _, chunk in shards)


def test_workers_capped_to_max(monkeypatch: pytest.MonkeyPatch) -> None:
    """An over-large --workers must clamp to MAX_WORKERS at pool creation.

    Each worker holds a full corpus store, so the cap bounds resident memory
    under the WSL2 ceiling. We assert the cap by observing the max_workers
    handed to the executor, and that output is still corpus-ordered.
    """
    seen: List[int] = []

    from lawvm.tools import _worker_pool

    orig = _worker_pool.managed_executor

    def _spy(workers: int, *args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
        seen.append(workers)
        return orig(workers, *args, **kwargs)

    monkeypatch.setattr(_worker_pool, "managed_executor", _spy)

    statute_ids = [str(i) for i in range(200)]
    ref = ("lawvm.tools._parallel_corpus", "_fake_projector")
    par_rows, par_diags = project_corpus_parallel(
        statute_ids=statute_ids,
        projector_ref=ref,
        serial_projector=_fake_projector,
        store=None,
        workers=64,  # far above the cap
    )

    assert seen, "executor was never created"
    assert max(seen) <= _parallel_corpus.MAX_WORKERS
    # Output is still byte-order-identical to the serial path.
    expected_rows, expected_diags = _expected_serial(statute_ids)
    assert par_rows == expected_rows
    assert par_diags == expected_diags


def test_empty_corpus() -> None:
    rows, diags = project_corpus_parallel(
        statute_ids=[],
        projector_ref=("lawvm.tools._parallel_corpus", "_fake_projector"),
        serial_projector=_fake_projector,
        store=None,
        workers=8,
    )
    assert rows == []
    assert diags == []
