"""Importable fake projector for the parallel-corpus determinism tests.

``project_corpus_parallel`` resolves the per-statute projector inside each
worker by ``(module, qualname)`` import — never by pickling the function. Under
Python 3.14 the default multiprocessing start method on Linux is ``forkserver``
(not ``fork``), so a worker re-imports this module from source: the projector
must therefore be a real, importable module attribute, not one injected onto a
module at test runtime. Keeping it in this dedicated fixtures module makes it
resolvable in a fresh worker process.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple


def _fake_projector(
    statute_id: str, store: Any
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Deterministic, order-sensitive projector.

    Emits a per-statute row encoding the statute id plus a variable (1..3) row
    count so shard boundaries cannot be masked by uniform row counts.
    """
    n = (int(statute_id) % 3) + 1  # 1..3 rows
    rows = [{"sid": statute_id, "k": i} for i in range(n)]
    diags = [{"sid": statute_id, "diag": True}]
    return rows, diags
