"""Tests for lawvm.tools._worker_pool.managed_executor."""
from __future__ import annotations

import time
from concurrent.futures import as_completed

import pytest

from lawvm.tools._worker_pool import managed_executor


def _identity(x: int) -> int:
    return x


def _slow_worker(x: int) -> int:
    """Worker that sleeps briefly — used to verify workers can be active."""
    time.sleep(0.05)
    return x * 2


def test_managed_executor_basic_results() -> None:
    """Workers produce correct results under normal operation."""
    inputs = list(range(10))
    with managed_executor(2) as pool:
        futures = [pool.submit(_identity, i) for i in inputs]
        results = sorted(f.result() for f in as_completed(futures))
    assert results == inputs


def test_managed_executor_terminates_workers_after_context() -> None:
    """After the context exits normally, no worker processes survive."""
    # We can't directly inspect child PIDs from outside, but we can verify
    # that the pool shuts down without hanging (i.e., workers exit within
    # the context's lifetime).
    with managed_executor(2) as pool:
        futs = [pool.submit(_slow_worker, i) for i in range(4)]
        results = [f.result() for f in as_completed(futs)]

    assert sorted(results) == [0, 2, 4, 6]


def test_managed_executor_reraises_keyboard_interrupt() -> None:
    """KeyboardInterrupt propagates out of the context manager."""
    with pytest.raises(KeyboardInterrupt):
        with managed_executor(2) as pool:
            raise KeyboardInterrupt


def test_managed_executor_with_initializer() -> None:
    """initializer/initargs are forwarded to ProcessPoolExecutor correctly."""

    def _init(val: int) -> None:
        # Store in a global so the worker can read it.  Not testing side
        # effects here — just verifying the pool starts without error.
        pass

    with managed_executor(2, initializer=_init, initargs=(42,)) as pool:
        result = pool.submit(_identity, 7).result()

    assert result == 7


def test_managed_executor_cleans_up_on_exception() -> None:
    """Pool is shut down even when an exception escapes the body."""
    class _Boom(Exception):
        pass

    with pytest.raises(_Boom):
        with managed_executor(2) as pool:
            _ = pool.submit(_identity, 1)
            raise _Boom("test")

    # If we reach here the finally block ran; no deadlock.
