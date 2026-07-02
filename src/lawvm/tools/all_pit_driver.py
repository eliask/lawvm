"""all_pit_driver.py — chunked, resumable driver for ``lawvm bench --mode all_pit`` (#187).

The single-shot all_pit path (:func:`lawvm.tools.bench._run_all_pit`) eagerly
``pool.submit``s EVERY statute into one ``ProcessPoolExecutor`` and holds all
futures + every ``_AllPitStatuteResult`` in the parent until ``as_completed``
drains. On a small ``--corpus`` list that is fine. On the full ~3546-statute
corpus it deadlocks at 0 CPU / exits 144 (OOM): there is no backpressure, so the
parent accumulates thousands of live results while N forked workers each hold a
copy-on-write corpus/index cache and run memory-heavy ``legal_pit`` replay across
every published snapshot. Peak RSS (parent accumulation + N worker replays)
exceeds the ceiling and the OS thrashes / OOM-kills before workers finish.

This driver fixes the ROOT CAUSE — unbounded fan-out with no result flushing — by
processing the corpus **chunk by chunk**:

* each chunk gets a FRESH bounded ``ProcessPoolExecutor``; only that chunk's
  statutes are in flight, so peak in-flight tasks ≤ ``chunk_size`` and peak
  worker RSS is bounded;
* the pool is torn down between chunks (releasing worker RSS) before the next
  chunk starts;
* each completed chunk's per-statute results are serialized to an on-disk
  **journal** (one JSON file per chunk under a run directory), so the parent
  never holds more than one chunk of results in RAM and a killed sweep RESUMES
  from the last completed chunk instead of restarting.

It is ADDITIVE: it never touches the single-shot all_pit path, and it reuses the
bench's own ``_all_pit_score_one_statute`` scorer and ``_show_all_pit_summary`` /
``_show_all_pit_anchor_touch`` aggregation. Only the DRIVER (fan-out + journal)
is new; scoring/attribution are unchanged, so the aggregate a chunked run
produces equals the single-shot aggregate over the same corpus.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Tuple

# Serialized journal schema tag — versioned so a format change is a conscious,
# diffable event rather than a silent reinterpretation of old journals.
ALL_PIT_JOURNAL_SCHEMA = "lawvm.all_pit_journal.v1"

_DEFAULT_CHUNK_SIZE = 64


# ---------------------------------------------------------------------------
# Chunk partition (deterministic)
# ---------------------------------------------------------------------------


def partition_chunks(sids: List[str], chunk_size: int) -> List[List[str]]:
    """Partition *sids* into contiguous chunks of at most *chunk_size*.

    Deterministic: input order is preserved and chunk boundaries depend only on
    ``chunk_size``, so the same corpus + chunk_size always yields identical
    chunk numbering (the journal resume key). ``chunk_size`` is clamped to ≥ 1.
    """
    size = max(1, int(chunk_size))
    return [sids[i : i + size] for i in range(0, len(sids), size)]


# ---------------------------------------------------------------------------
# Journal — one file per completed chunk under a run directory
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChunkRecord:
    """One completed chunk's persisted results.

    ``results`` are the per-statute dicts produced by
    :func:`_statute_result_to_dict`; they round-trip back into
    ``_AllPitStatuteResult`` via :func:`_statute_result_from_dict`.
    """

    chunk_index: int
    sids: Tuple[str, ...]
    results: Tuple[dict, ...]

    def to_dict(self) -> dict:
        return {
            "schema": ALL_PIT_JOURNAL_SCHEMA,
            "chunk_index": self.chunk_index,
            "sids": list(self.sids),
            "results": list(self.results),
        }


def _chunk_journal_path(run_dir: Path, chunk_index: int) -> Path:
    return run_dir / f"chunk_{chunk_index:05d}.json"


def _run_manifest_path(run_dir: Path) -> Path:
    return run_dir / "run_manifest.json"


def write_chunk_record(run_dir: Path, record: ChunkRecord) -> None:
    """Persist one chunk atomically (write temp, then rename)."""
    run_dir.mkdir(parents=True, exist_ok=True)
    path = _chunk_journal_path(run_dir, record.chunk_index)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(record.to_dict(), fh, sort_keys=True, ensure_ascii=False, indent=2)
        fh.write("\n")
    tmp.replace(path)


def read_chunk_record(run_dir: Path, chunk_index: int) -> Optional[ChunkRecord]:
    """Read a persisted chunk, or ``None`` if absent/malformed.

    A malformed/partial chunk (e.g. a crash mid-write that somehow survived the
    atomic rename) is treated as absent so the chunk is recomputed — never a
    silent skip.
    """
    path = _chunk_journal_path(run_dir, chunk_index)
    if not path.exists():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    if doc.get("schema") != ALL_PIT_JOURNAL_SCHEMA:
        return None
    try:
        return ChunkRecord(
            chunk_index=int(doc["chunk_index"]),
            sids=tuple(doc["sids"]),
            results=tuple(doc["results"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _write_run_manifest(
    run_dir: Path, *, sids: List[str], chunk_size: int, workers: int, n_chunks: int
) -> None:
    """Persist the run's identity so resume can verify corpus/chunking match."""
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": ALL_PIT_JOURNAL_SCHEMA,
        "chunk_size": int(chunk_size),
        "workers": int(workers),
        "n_chunks": int(n_chunks),
        "statute_count": len(sids),
        # A digest of the ordered corpus so a resume against a DIFFERENT corpus
        # (or reordered) is detected and refused rather than silently mixing
        # stale chunk journals with fresh statutes.
        "corpus_digest": _corpus_digest(sids),
    }
    _run_manifest_path(run_dir).write_text(
        json.dumps(manifest, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _corpus_digest(sids: List[str]) -> str:
    import hashlib

    return hashlib.sha256("\n".join(sids).encode("utf-8")).hexdigest()


def _read_run_manifest(run_dir: Path) -> Optional[dict]:
    path = _run_manifest_path(run_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


# ---------------------------------------------------------------------------
# Result serialization (round-trips _AllPitStatuteResult <-> dict)
# ---------------------------------------------------------------------------


def _statute_result_to_dict(res: Any) -> dict:
    """Serialize an ``_AllPitStatuteResult`` to a plain dict for the journal."""
    return {
        "sid": res.sid,
        "phase_status": res.phase_status,
        "snapshots": [
            {
                "version_tag": s.version_tag,
                "amendment_id": s.amendment_id,
                "as_of": s.as_of,
                "struct_sim": s.struct_sim,
                "n_sections": s.n_sections,
                "n_penalized": s.n_penalized,
                "phase_status": s.phase_status,
            }
            for s in res.snapshots
        ],
    }


def _statute_result_from_dict(doc: dict) -> Any:
    """Rehydrate an ``_AllPitStatuteResult`` from a journal dict.

    Imports the bench dataclasses lazily so this module stays importable without
    dragging in the heavy bench/grafter import chain at parse time.
    """
    from lawvm.tools.bench import _AllPitSnapshotResult, _AllPitStatuteResult

    snapshots = tuple(
        _AllPitSnapshotResult(
            version_tag=s["version_tag"],
            amendment_id=s["amendment_id"],
            as_of=s["as_of"],
            struct_sim=float(s["struct_sim"]),
            n_sections=int(s["n_sections"]),
            n_penalized=int(s["n_penalized"]),
            phase_status=s["phase_status"],
        )
        for s in doc.get("snapshots", [])
    )
    return _AllPitStatuteResult(
        sid=doc["sid"],
        snapshots=snapshots,
        phase_status=doc.get("phase_status", "OK"),
    )


# ---------------------------------------------------------------------------
# Chunk execution (bounded pool, fresh per chunk)
# ---------------------------------------------------------------------------


def _prewarm_parent_caches() -> None:
    """Pre-warm the caches forked workers inherit via copy-on-write.

    Identical to the single-shot ``_run_all_pit`` pre-warm, kept here so the
    chunked driver has the same COW-inheritance behavior.
    """
    from lawvm.finland.amendment_selection import (
        amendment_children_by_parent as _amendment_children_by_parent,
    )
    from lawvm.finland.corpus import (
        _get_corpus_store,
        _latest_consolidated_path_by_statute,
    )

    _get_corpus_store()
    _amendment_children_by_parent()
    _latest_consolidated_path_by_statute()


def _run_chunk(chunk_sids: List[str], *, workers: int) -> List[Any]:
    """Score one chunk with a FRESH bounded pool that is torn down on return.

    Peak in-flight tasks ≤ ``len(chunk_sids)``; the pool's context manager joins
    and reaps all workers before returning, releasing their RSS before the next
    chunk starts. Results come back in ``chunk_sids`` order (deterministic).
    """
    from lawvm.tools.bench import _all_pit_score_one_statute

    total = len(chunk_sids)
    if workers <= 1 or total <= 1:
        return [_all_pit_score_one_statute(sid) for sid in chunk_sids]

    from concurrent.futures import ProcessPoolExecutor

    pool_workers = max(1, min(workers, total))
    results: List[Any] = [None] * total  # type: ignore[list-item]
    with ProcessPoolExecutor(max_workers=pool_workers) as pool:
        # map preserves input order and bounds the submit set to this chunk.
        for idx, res in enumerate(pool.map(_all_pit_score_one_statute, chunk_sids)):
            results[idx] = res
    return results


# ---------------------------------------------------------------------------
# Top-level chunked driver
# ---------------------------------------------------------------------------


def _default_run_dir() -> Path:
    """LawVM/data/all_pit_runs/current — sibling of the bench run store."""
    here = Path(__file__).resolve()
    # src/lawvm/tools/all_pit_driver.py -> src/lawvm/tools -> ... -> LawVM/data
    return here.parent.parent.parent.parent / "data" / "all_pit_runs" / "current"


def run_all_pit_chunked(
    sids: List[str],
    *,
    workers: int,
    chunk_size: int = _DEFAULT_CHUNK_SIZE,
    run_dir: Optional[Path] = None,
    resume: bool = True,
    verbose: bool = True,
) -> List[Any]:
    """Chunked, resumable all_pit sweep. Returns the same ``_AllPitStatuteResult``
    list the single-shot ``_run_all_pit`` returns (in corpus order).

    * **Chunking** bounds peak memory/process fan-out (the deadlock fix).
    * **Resumability**: each completed chunk is journaled under *run_dir*; a
      resumed run reloads completed chunks and only computes the missing ones.
    * **Deterministic**: chunk numbering + within-chunk order are stable.
    """
    if run_dir is None:
        run_dir = _default_run_dir()
    run_dir = Path(run_dir)

    chunks = partition_chunks(sids, chunk_size)
    n_chunks = len(chunks)

    # Resume guard: if a manifest exists for a DIFFERENT corpus/chunking, refuse
    # to silently reuse stale journals — start a clean run instead (logged).
    prior = _read_run_manifest(run_dir) if resume else None
    if prior is not None:
        stale = (
            prior.get("corpus_digest") != _corpus_digest(sids)
            or int(prior.get("chunk_size") or 0) != int(chunk_size)
        )
        if stale:
            if verbose:
                print(
                    f"[all_pit chunked] run dir {run_dir} holds a DIFFERENT corpus/"
                    f"chunk_size; ignoring stale journals and starting fresh.",
                    flush=True,
                )
            resume = False

    if not resume:
        _clear_run_dir(run_dir)
    _write_run_manifest(
        run_dir, sids=sids, chunk_size=chunk_size, workers=workers, n_chunks=n_chunks
    )

    if verbose:
        print(
            f"[all_pit chunked] {len(sids)} statutes  chunk_size={chunk_size}  "
            f"chunks={n_chunks}  workers={workers}  run_dir={run_dir}  "
            f"resume={'on' if resume else 'off'}",
            flush=True,
        )

    if workers > 1:
        _prewarm_parent_caches()

    all_results: List[Any] = []
    running_scored = 0
    running_perfect = 0
    t0 = time.monotonic()
    for chunk_index, chunk_sids in enumerate(chunks):
        record = read_chunk_record(run_dir, chunk_index) if resume else None
        if record is not None and list(record.sids) == chunk_sids:
            results = [_statute_result_from_dict(d) for d in record.results]
            source = "resumed"
        else:
            results = _run_chunk(chunk_sids, workers=workers)
            write_chunk_record(
                run_dir,
                ChunkRecord(
                    chunk_index=chunk_index,
                    sids=tuple(chunk_sids),
                    results=tuple(_statute_result_to_dict(r) for r in results),
                ),
            )
            source = "computed"
        all_results.extend(results)

        # Running perfect-rate over scored snapshots (observable progress).
        for r in results:
            for s in getattr(r, "snapshots", ()):
                if s.struct_sim >= 0.0:
                    running_scored += 1
                    if s.struct_sim >= 1.0 - 1e-9:
                        running_perfect += 1
        if verbose:
            elapsed = time.monotonic() - t0
            rate = (
                f"{100 * running_perfect / running_scored:.1f}%"
                if running_scored
                else "n/a"
            )
            print(
                f"[all_pit chunked] chunk {chunk_index + 1}/{n_chunks} "
                f"({source}, {len(chunk_sids)} statutes)  "
                f"running-perfect-snapshot={rate} "
                f"({running_perfect}/{running_scored})  "
                f"elapsed={elapsed:.1f}s",
                flush=True,
            )

    return all_results


def _clear_run_dir(run_dir: Path) -> None:
    """Remove any prior chunk journals + manifest so a fresh run is clean."""
    if not run_dir.exists():
        return
    for path in run_dir.glob("chunk_*.json"):
        path.unlink()
    for path in run_dir.glob("chunk_*.json.tmp"):
        path.unlink()
    manifest = _run_manifest_path(run_dir)
    if manifest.exists():
        manifest.unlink()
