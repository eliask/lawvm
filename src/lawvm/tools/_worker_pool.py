"""Managed worker pool with cleanup guarantees.

Provides a context manager for ProcessPoolExecutor that ensures worker
processes are terminated on exit, signal (SIGTERM/SIGINT), or crash.
Without this, workers forked from a killed parent survive as orphans.

Also provides :func:`open_farchive_immutable` — a shared helper for parallel
bench workers that need a concurrent-safe read-only Farchive handle.  See its
docstring for the WSL2 / ``immutable=1`` rationale.
"""
from __future__ import annotations

import atexit
import signal
import types
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from logging import getLogger
from pathlib import Path
from typing import Any, Callable, Iterator, Optional, cast

from lawvm.core.named_swallow import named_swallow

_logger = getLogger(__name__)


def open_farchive_immutable(db_path: Path) -> Any:
    """Open a Farchive as an ``immutable`` read-only SQLite snapshot.

    Unlike ``Farchive(path, readonly=True)`` — which uses ``mode=ro`` and
    therefore still consults the ``-wal``/``-shm`` sidecar files — this opens
    the database with the ``immutable=1`` URI parameter.  That tells SQLite
    the file (and any WAL) cannot change under it, so it maps the main database
    file directly and never touches ``-wal``/``-shm``.

    This matters for concurrent parallel readers on WSL2: when many worker
    processes open the same multi-gigabyte archive simultaneously, ``mode=ro``
    opens race on the shared ``-shm`` mapping and surface
    ``sqlite3.OperationalError: disk I/O error`` (and read-WRITE opens surface
    ``unable to open database file`` from WAL/writer-lock setup).
    ``immutable=1`` sidesteps both by never engaging the WAL machinery.

    Correctness contract: the caller must guarantee no writer is committing to
    the archive during the read.  For bench replay paths that only read static
    corpus blobs this is always true — any WAL present at open time has already
    been checkpointed into the main file.  Use ONLY for read-only consumers
    such as parallel bench workers — never for acquisition / write paths.

    This function is the jurisdiction-neutral factoring of
    ``lawvm.estonia.fetch.open_rt_archive_immutable``.  It is kept in lockstep
    with ``farchive._archive.Farchive.__init__`` read-only branch — if that
    constructor's attribute set changes, this must follow.
    """
    import sqlite3

    from farchive import Farchive
    from farchive._archive import (  # type: ignore[import-not-found]
        SCHEMA_VERSION,
        CompressionPolicy,
        detect_schema_version,
    )

    # immutable=1: SQLite treats the DB (and any WAL) as unchanging and maps
    # the main file directly, never opening ``-wal``/``-shm``.
    url = db_path.resolve().as_uri() + "?immutable=1"
    conn = sqlite3.connect(url, uri=True)
    conn.row_factory = sqlite3.Row

    version = detect_schema_version(conn)
    if version != SCHEMA_VERSION:
        conn.close()
        raise RuntimeError(
            f"Farchive DB version {version} is incompatible with current "
            f"SCHEMA_VERSION={SCHEMA_VERSION}. Please run 'farchive migrate'."
        )

    archive = Farchive.__new__(Farchive)
    archive._db_path = db_path
    archive._policy = CompressionPolicy()
    archive._readonly = True
    archive._conn = conn
    archive._events_enabled = (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='event'"
        ).fetchone()
        is not None
    )
    archive._dict_cache = {}
    archive._has_dict_for_class = {}
    archive._lock_path = db_path.with_name(db_path.name + ".writer.lock")
    archive._lock_held = False
    archive._supports_span_series_key = archive._has_column("locator_span", "series_key")
    return archive


@contextmanager
def managed_executor(
    workers: int,
    initializer: Optional[Any] = None,
    initargs: tuple[Any, ...] = (),
) -> Iterator[ProcessPoolExecutor]:
    """Context manager wrapping ProcessPoolExecutor with guaranteed cleanup.

    Registers an atexit handler and SIGTERM/SIGINT handlers so that worker
    processes are forcibly terminated if the parent exits unexpectedly.

    Usage::

        with managed_executor(8) as pool:
            futures = [pool.submit(fn, item) for item in items]
            for f in as_completed(futures):
                ...

    The context manager also correctly handles KeyboardInterrupt: workers
    are cancelled and the interrupt is re-raised to the caller.
    """
    kwargs: dict[str, Any] = {"max_workers": workers}
    if initializer is not None:
        kwargs["initializer"] = initializer
        kwargs["initargs"] = initargs

    executor = ProcessPoolExecutor(**kwargs)

    def _terminate() -> None:
        # ``executor.shutdown`` can race with signal-handler-driven teardown
        # and raise acrossAsyncResult / BrokenProcessPool paths. Previously
        # ``except Exception: pass`` silently swallowed to a no-op
        # (AGENTS.md §1.10 silent-fallback). Now wrapped in ``named_swallow``
        # so a typed Finding is logged at WARNING carrying the offending
        # shutdown exception. The default is None (no return value used);
        # emit-via-log keeps the swallow visible without aborting atexit-driven
        # teardown during interpreter shutdown.
        #
        # log_emitter sanctioned (iter3 W2 §3.2 evidence-path audit):
        # this swallow fires inside atexit + signal-handler-driven teardown
        # paths (no caller-stack scope at all). There is no findings_out
        # accumulator to plumb here — the teardown runs at process exit/signal,
        # not within a per-statute compile fold. Per ``core/named_swallow.py``
        # docstring's IO/utility-boundary sanctioned use, the swallow stays on
        # the inline ``emit=lambda`` (stderr WARNING via ``_logger.warning``);
        # per-statute evidence-ledger reach is structurally not applicable.
        with named_swallow(
            rule_id="tools_worker_pool_terminate_shutdown",
            default=None,
            jurisdiction=None,
            clause_text=f"max_workers={workers} initializer={getattr(initializer, '__name__', None)}",
            emit=lambda f: _logger.warning(
                "named_swallow[%s] during executor shutdown: %s",
                f.detail.get("rule_id", ""),
                f.detail.get("exception_message", ""),
            ),
        ):
            # cancel_futures=True requires Python 3.9+; available everywhere
            # LawVM runs.
            executor.shutdown(wait=False, cancel_futures=True)

    atexit.register(_terminate)

    # getsignal() returns Handlers | Callable | None; we preserve the raw value
    # for signal.signal() but use a typed alias when calling it.
    _SigHandler = Callable[[int, Optional[types.FrameType]], Any]
    old_sigterm = signal.getsignal(signal.SIGTERM)
    old_sigint = signal.getsignal(signal.SIGINT)

    def _signal_handler(signum: int, frame: Any) -> None:
        _terminate()
        # Restore original handler and re-raise so callers / shells get the
        # correct exit status.
        if signum == signal.SIGTERM:
            signal.signal(signal.SIGTERM, old_sigterm)
            if callable(old_sigterm):
                cast(_SigHandler, old_sigterm)(signum, frame)
            else:
                raise SystemExit(128 + signum)
        else:  # SIGINT
            signal.signal(signal.SIGINT, old_sigint)
            if callable(old_sigint):
                cast(_SigHandler, old_sigint)(signum, frame)
            else:
                raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    try:
        yield executor
    except KeyboardInterrupt:
        _terminate()
        raise
    finally:
        # Normal exit: clean shutdown (wait for in-flight work to finish).
        # If _terminate() was already called (signal path) this is a no-op.
        # Wrap in ``named_swallow`` so a shutdown failure during finally is
        # witnessed (WARNING) rather than silently swallowed — AND let the
        # ``finally`` block continue to unregister atexit/signal handlers so
        # the process does not leak state.
        #
        # log_emitter sanctioned (iter3 W2 §3.2 evidence-path audit):
        # this swallow fires in the ``finally`` of the managed-executor
        # contextmanager teardown (signal/atexit-adjacent). There is no
        # findings_out accumulator in scope here — the teardown runs at
        # context-exit, not within any per-statute compile fold. Per
        # ``core/named_swallow.py`` docstring's IO/utility-boundary sanctioned
        # use, the swallow stays on the inline ``emit=lambda`` (stderr WARNING
        # via ``_logger.warning``); per-statute evidence-ledger reach is
        # structurally not applicable.
        with named_swallow(
            rule_id="tools_worker_pool_cleanup_shutdown",
            default=None,
            jurisdiction=None,
            clause_text=f"max_workers={workers} cleanup=normal-exit",
            emit=lambda f: _logger.warning(
                "named_swallow[%s] during finally executor.shutdown: %s",
                f.detail.get("rule_id", ""),
                f.detail.get("exception_message", ""),
            ),
        ):
            executor.shutdown(wait=True)
        atexit.unregister(_terminate)
        signal.signal(signal.SIGTERM, old_sigterm)
        signal.signal(signal.SIGINT, old_sigint)
