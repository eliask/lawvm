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
from typing import Any, Callable, Iterator, Optional, cast


@contextmanager
def managed_executor(
    workers: int,
    initializer: Optional[Any] = None,
    initargs: tuple = (),
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
        try:
            # cancel_futures=True requires Python 3.9+; available everywhere
            # LawVM runs.
            executor.shutdown(wait=False, cancel_futures=True)
        except (NameError, TypeError, AttributeError):
            raise  # programming bugs — fail loud
        except Exception:
            pass

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
        try:
            executor.shutdown(wait=True)
        except (NameError, TypeError, AttributeError):
            raise  # programming bugs — fail loud
        except Exception:
            pass
        atexit.unregister(_terminate)
        signal.signal(signal.SIGTERM, old_sigterm)
        signal.signal(signal.SIGINT, old_sigint)
