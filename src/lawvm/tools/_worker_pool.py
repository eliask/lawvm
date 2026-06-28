"""Managed worker pool with cleanup guarantees.

Provides a context manager for ProcessPoolExecutor that ensures worker
processes are terminated on exit, signal (SIGTERM/SIGINT), or crash.
Without this, workers forked from a killed parent survive as orphans.
"""
from __future__ import annotations

import atexit
import signal
import types
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from logging import getLogger
from typing import Any, Callable, Iterator, Optional, cast

from lawvm.core.named_swallow import named_swallow

_logger = getLogger(__name__)


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
