"""Default-corpus memoization for the CTSF ``score_*_real_corpus`` scorers.

Every ``score_<jur>_real_corpus`` function is, by contract, a *pure deterministic*
projection of the frozen corpus bytes into a small ``{sid: {family: count}}`` typed-
residual set (see each scorer's docstring: "Deterministic given the frozen corpus
bytes"). The CTSF gate test suite calls each scorer 4–6 times per jurisdiction (a
determinism test alone calls it twice back-to-back), and every real call site invokes
it with **no argument** (the frozen default corpus). Each such call re-opens the
jurisdiction Farchive and re-runs the full per-anchor replay — the single dominant
memory (and time) cost of the ``tools_cli_debug`` shard, where a heavy corpus replay
(EE 12.6 GB / UK 5.8 GB / FI 5.9 GB archive) transiently peaks multiple GB of replay
IR.

``memoize_default_corpus`` caches ONLY the default (argument-less) call. A call that
passes an explicit ``sids``/``windows`` iterable bypasses the cache entirely and runs
the live replay, so parametrized/synthetic callers are unaffected. The cached object
is tiny (a nested count dict), so retaining it across the process costs a few KB while
collapsing N repeated multi-GB replays into ONE — the redundant replays (and their
transient IR peaks) simply never happen.

A ``copy.deepcopy`` is returned on every call so a caller that mutates the returned
dict cannot poison the shared cache; the result is byte-identical to a fresh replay.
"""
from __future__ import annotations

import copy
import functools
from typing import Any, Callable, TypeVar

_Result = TypeVar("_Result")

# Shared memo of default-corpus results, keyed by the wrapped scorer function.
# ``clear_all_corpus_caches()`` drops it for tests/tooling wanting a cold replay.
_CACHE: dict[Callable[..., Any], Any] = {}


def memoize_default_corpus(
    func: Callable[..., _Result],
) -> Callable[..., _Result]:
    """Memoize ``func`` for the argument-less (frozen-default-corpus) call only.

    ``func`` must accept a single optional positional/keyword argument (the corpus
    selector, e.g. ``sids`` or ``windows``) that defaults to ``None``. The default
    (``None``/omitted) call is memoized; any explicit non-``None`` selector bypasses
    the cache and calls ``func`` live.
    """

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> _Result:
        # Only the pure default call (no positional selector, no keyword overrides,
        # or an explicit ``None`` selector) is memoized. Any explicit argument —
        # a corpus subset or an alternate ``data_dir`` — bypasses the cache and
        # runs the live replay, preserving exact call semantics.
        default_call = (not kwargs) and (
            not args or (len(args) == 1 and args[0] is None)
        )
        if not default_call:
            return func(*args, **kwargs)
        if func not in _CACHE:
            _CACHE[func] = func()
        # Defensive copy: callers must not be able to mutate the shared snapshot.
        return copy.deepcopy(_CACHE[func])

    return wrapper


def clear_all_corpus_caches() -> None:
    """Drop every memoized default-corpus result (forces a cold replay next call)."""
    _CACHE.clear()
