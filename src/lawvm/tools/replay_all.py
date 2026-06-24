"""Instrumented full-corpus replay (`lawvm replay-all`).

Enumerates EVERY statute id in the full farchive — the same enumeration that
``export-projections`` uses for ``corpus='all'`` (``CorpusStore.list_statute_ids``
via ``lawvm.finland.corpus.get_corpus_store``) — and runs the production FI
replay pipeline (``replay_xml`` through the typed ``ReplayXmlRequest`` /
``ReplayXmlSinks`` boundary) once per statute.

This is a measurement / ops command: it does NOT change replay semantics. Its
purpose is to exercise every reachable ``src/lawvm/finland/`` code path so that
coverage tooling can classify modules as never-touched / marginal / hot. A
zero-amendment statute still drives the source-materialization path and is
included; there is NO amendment filter.

Robust to per-statute failures: failures are counted and the run continues.
"""

from __future__ import annotations

import sys
import time
from typing import Any, List, Optional, Tuple


def _enumerate_statute_ids() -> List[str]:
    """Enumerate every statute id in the full farchive.

    Reuses the exact corpus source ``export-projections --corpus all`` uses, so
    the measured scope is the full ~59,574-statute farchive including
    zero-amendment statutes.
    """
    from lawvm.finland.corpus import get_corpus_store

    store = get_corpus_store(readonly=True)
    return list(store.list_statute_ids())


def _replay_one(statute_id: str, mode: str) -> Tuple[str, bool, str]:
    """Run the production FI replay pipeline for one statute.

    Returns ``(statute_id, ok, status)``. ``ok`` is True when ``replay_xml``
    returned without raising; ``status`` carries the exception text on failure.
    """
    from typing import Literal, cast

    from lawvm.finland.replay_entrypoint import replay_xml
    from lawvm.finland.replay_request import (
        ReplayXmlRequest,
        ReplayXmlSinks,
        call_replay_xml,
    )

    compiled_ops: list[dict[str, object]] = []
    try:
        call_replay_xml(
            replay_xml,
            request=ReplayXmlRequest(
                parent_id=statute_id,
                mode=cast(
                    Literal["official_consolidation", "legal_pit"], mode
                ),
                quiet=True,
                build_full_products=True,
            ),
            sinks=ReplayXmlSinks(compiled_ops_out=compiled_ops),
        )
    except (NameError, TypeError, AttributeError):
        # Programming errors are not per-statute data faults — surface them.
        raise
    except Exception as exc:  # noqa: BLE001 — count + continue
        return statute_id, False, str(exc)
    return statute_id, True, ""


def _replay_worker(args: Tuple[str, str]) -> Tuple[str, bool, str]:
    statute_id, mode = args
    return _replay_one(statute_id, mode)


def _coverage_worker_init() -> None:
    """Pool initializer: start coverage in each worker.

    When the run is under ``coverage`` with ``concurrency=multiprocessing``,
    coverage's automatic ``multiprocessing`` patching does not reliably reach
    workers launched through an explicit start-method context. Calling
    ``coverage.process_startup()`` here is the documented, robust way to ensure
    every worker records its own coverage data file. A no-op when coverage is
    not active (``COVERAGE_PROCESS_START`` unset).
    """
    import os

    if not os.environ.get("COVERAGE_PROCESS_START"):
        return
    try:
        import coverage

        coverage.process_startup()
    except Exception:  # noqa: BLE001 — coverage is optional/best-effort
        pass


def main(args: Any) -> int:
    mode: str = getattr(args, "mode", "official_consolidation")
    workers: int = int(getattr(args, "workers", 1) or 1)
    limit: Optional[int] = getattr(args, "limit", None)

    jurisdiction = getattr(args, "jurisdiction", "fi") or "fi"
    if jurisdiction not in (None, "fi"):
        print(
            f"replay-all: only jurisdiction 'fi' is supported (got {jurisdiction!r})",
            file=sys.stderr,
        )
        return 2

    shard_spec: Optional[str] = getattr(args, "shard", None)
    shard_i = 0
    shard_n = 1
    if shard_spec:
        try:
            i_str, n_str = shard_spec.split("/", 1)
            shard_i, shard_n = int(i_str), int(n_str)
        except ValueError:
            print(
                f"replay-all: --shard must be of the form I/N (got {shard_spec!r})",
                file=sys.stderr,
            )
            return 2
        if not (0 <= shard_i < shard_n) or shard_n < 1:
            print(
                f"replay-all: --shard I/N requires 0 <= I < N (got {shard_spec!r})",
                file=sys.stderr,
            )
            return 2

    print("replay-all: enumerating full farchive corpus ...", file=sys.stderr)
    statute_ids = _enumerate_statute_ids()
    total_in_corpus = len(statute_ids)
    if limit is not None and limit >= 0:
        statute_ids = statute_ids[:limit]
    if shard_n > 1:
        # Stride sharding for balanced per-shard load. Disjoint + exhaustive
        # across shards 0..N-1, so the union covers the entire corpus exactly
        # once — letting N independent single-process invocations be combined
        # for a correct full-corpus coverage map without multiprocessing.
        statute_ids = statute_ids[shard_i::shard_n]
    attempted = len(statute_ids)
    print(
        f"replay-all: corpus has {total_in_corpus} statutes; "
        f"replaying {attempted} (mode={mode}, workers={workers}, "
        f"shard={shard_i}/{shard_n})",
        file=sys.stderr,
    )

    replayed = 0
    failed = 0
    failures: List[Tuple[str, str]] = []
    t0 = time.time()

    def _record(statute_id: str, ok: bool, status: str, idx: int) -> None:
        nonlocal replayed, failed
        if ok:
            replayed += 1
        else:
            failed += 1
            if len(failures) < 200:
                failures.append((statute_id, status))
        if idx % 500 == 0 or idx == attempted:
            elapsed = time.time() - t0
            rate = idx / elapsed if elapsed > 0 else 0.0
            print(
                f"replay-all: [{idx}/{attempted}] replayed={replayed} "
                f"failed={failed} ({rate:.1f}/s)",
                file=sys.stderr,
                flush=True,
            )

    if workers <= 1:
        for idx, statute_id in enumerate(statute_ids, start=1):
            sid, ok, status = _replay_one(statute_id, mode)
            _record(sid, ok, status, idx)
    else:
        import multiprocessing as mp
        import os

        # When run under coverage with concurrency=multiprocessing, the worker
        # processes must be started via 'spawn' so the coverage subprocess hook
        # (COVERAGE_PROCESS_START) fires in each child; forked children inherit
        # the parent without re-running the startup hook and go uninstrumented.
        # Production (no coverage) keeps the faster default start method.
        ctx: Any = mp
        if os.environ.get("COVERAGE_PROCESS_START"):
            ctx = mp.get_context("spawn")

        work = [(sid, mode) for sid in statute_ids]
        with ctx.Pool(processes=workers, initializer=_coverage_worker_init) as pool:
            for idx, (sid, ok, status) in enumerate(
                pool.imap_unordered(_replay_worker, work, chunksize=8), start=1
            ):
                _record(sid, ok, status, idx)

    elapsed = time.time() - t0
    print("", file=sys.stderr)
    print("=== replay-all summary ===", file=sys.stderr)
    print(f"corpus total : {total_in_corpus}", file=sys.stderr)
    print(f"attempted    : {attempted}", file=sys.stderr)
    print(f"replayed ok  : {replayed}", file=sys.stderr)
    print(f"failed       : {failed}", file=sys.stderr)
    print(f"elapsed      : {elapsed:.1f}s", file=sys.stderr)
    if failures:
        print(
            f"first {len(failures)} failures (statute_id : status):",
            file=sys.stderr,
        )
        for sid, status in failures[:50]:
            short = status.splitlines()[0][:160] if status else ""
            print(f"  {sid} : {short}", file=sys.stderr)

    return 0
