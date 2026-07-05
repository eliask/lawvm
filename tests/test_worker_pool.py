"""Tests for lawvm.tools._worker_pool — managed_executor and open_farchive_immutable."""
from __future__ import annotations

import pickle
import time
from concurrent.futures import as_completed
from pathlib import Path

import pytest

from lawvm.tools._worker_pool import managed_executor, open_farchive_immutable


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


def _noop_init(val: int) -> None:
    # Module-level so it is picklable for the forkserver/spawn worker init
    # (Python 3.14's default Linux start method is no longer fork). Just
    # verifies the pool starts with an initializer; no side effects tested.
    pass


def test_managed_executor_with_initializer() -> None:
    """initializer/initargs are forwarded to ProcessPoolExecutor correctly."""
    with managed_executor(2, initializer=_noop_init, initargs=(42,)) as pool:
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


# ---------------------------------------------------------------------------
# open_farchive_immutable tests
# ---------------------------------------------------------------------------


def test_open_farchive_immutable_opens_real_archive(tmp_path: Path) -> None:
    """open_farchive_immutable opens an existing Farchive in read-only mode.

    Creates a minimal real farchive (write-mode), closes it, then opens it
    immutable and verifies the handle is read-only and operational.
    """
    from farchive import Farchive

    db = tmp_path / "test.farchive"
    # Create a valid farchive so the schema version matches.
    arch = Farchive(db)
    arch.close()

    immutable = open_farchive_immutable(db)
    try:
        assert immutable._readonly is True
        assert immutable._db_path == db
        # The connection must be live (not closed).
        immutable._conn.execute("SELECT 1")
    finally:
        immutable.close()


def test_open_farchive_immutable_raises_on_invalid_db(tmp_path: Path) -> None:
    """open_farchive_immutable raises RuntimeError for a wrong-version DB."""
    # SQLite ``immutable=1`` does not raise on a missing file (it creates an
    # empty connection), but an empty DB has schema version 0 != SCHEMA_VERSION
    # so open_farchive_immutable raises a descriptive RuntimeError.
    bad = tmp_path / "bad.farchive"
    # Create an empty (non-farchive) SQLite file so the connect succeeds.
    import sqlite3

    sqlite3.connect(str(bad)).close()
    with pytest.raises(RuntimeError, match="SCHEMA_VERSION"):
        open_farchive_immutable(bad)


# ---------------------------------------------------------------------------
# Forkserver-safe initializer picklability tests (#210 / #223)
# ---------------------------------------------------------------------------
#
# Under Python 3.14 the default Linux multiprocessing start method is
# ``forkserver`` (not ``fork``).  ProcessPoolExecutor initializer functions
# must be picklable module-level callables — local closures or lambdas fail
# with ``AttributeError`` in forkserver workers.  These tests verify that each
# jurisdiction's parallel bench initializer satisfies that contract.


def test_uk_bench_worker_initializer_is_picklable() -> None:
    """_configure_uk_bench_worker is a module-level function (picklable for forkserver)."""
    from lawvm.tools.uk_bench import _configure_uk_bench_worker

    pickled = pickle.dumps(_configure_uk_bench_worker)
    assert pickle.loads(pickled) is _configure_uk_bench_worker


def test_no_bench_worker_initializer_is_picklable() -> None:
    """_init_no_bench_worker is a module-level function (picklable for forkserver)."""
    from lawvm.tools.no_bench import _init_no_bench_worker

    pickled = pickle.dumps(_init_no_bench_worker)
    assert pickle.loads(pickled) is _init_no_bench_worker


def test_nz_bench_worker_init_is_picklable() -> None:
    """nz_bench._worker_init is a module-level function (picklable for forkserver)."""
    from lawvm.tools.nz_bench import _worker_init as nz_worker_init

    pickled = pickle.dumps(nz_worker_init)
    assert pickle.loads(pickled) is nz_worker_init


def test_ee_bench_worker_initializer_is_picklable() -> None:
    """ee_bench._init_worker is a module-level function (picklable for forkserver)."""
    from lawvm.tools.ee_bench import _init_worker as ee_init_worker

    pickled = pickle.dumps(ee_init_worker)
    assert pickle.loads(pickled) is ee_init_worker


def test_us_bench_worker_init_is_picklable() -> None:
    """us_federal bench._worker_init is a module-level function (picklable for forkserver)."""
    from lawvm.us_federal.bench import _worker_init as us_worker_init

    pickled = pickle.dumps(us_worker_init)
    assert pickle.loads(pickled) is us_worker_init
