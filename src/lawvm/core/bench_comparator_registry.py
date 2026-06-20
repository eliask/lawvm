"""Per-jurisdiction benchmark comparator registry.

The comparator is the *only* jurisdiction-specific part of the unified bench
contract: each jurisdiction's oracle ontology is genuinely different, so each
registers a callable that turns its own oracle/replay inputs into a contract
:class:`BenchUnitResult` carrying the two error axes.

This mirrors the existing ``semantic/projection.py`` registry pattern (a
jurisdiction-keyed dispatch table populated at import time). The shared harness
and reporting layers consume :class:`BenchUnitResult`s and never import a
jurisdiction package directly.

A comparator's signature is jurisdiction-defined (its inputs differ); the only
contract is its **return type** — a :class:`BenchUnitResult`.
"""

from __future__ import annotations

from typing import Any, Callable

from lawvm.core.bench_contract import BenchUnitResult

BenchComparator = Callable[..., BenchUnitResult]

_COMPARATORS: dict[str, BenchComparator] = {}


def register_bench_comparator(jurisdiction: str, fn: BenchComparator) -> None:
    """Register the comparator that emits :class:`BenchUnitResult`s for *jurisdiction*.

    Registering twice for the same key overwrites — a jurisdiction owns its key.
    """
    if not jurisdiction:
        raise ValueError("jurisdiction key must be non-empty")
    _COMPARATORS[jurisdiction] = fn


def get_bench_comparator(jurisdiction: str) -> BenchComparator:
    """Return the registered comparator for *jurisdiction*.

    Raises :class:`KeyError` (fail loud) when none is registered, rather than
    silently falling back to a default that would produce misleading rows.
    """
    try:
        return _COMPARATORS[jurisdiction]
    except KeyError as exc:
        raise KeyError(
            f"no bench comparator registered for jurisdiction {jurisdiction!r}; "
            f"registered: {sorted(_COMPARATORS)}"
        ) from exc


def has_bench_comparator(jurisdiction: str) -> bool:
    return jurisdiction in _COMPARATORS


def registered_jurisdictions() -> tuple[str, ...]:
    return tuple(sorted(_COMPARATORS))


def run_bench_comparator(jurisdiction: str, *args: Any, **kwargs: Any) -> BenchUnitResult:
    """Dispatch to the registered comparator and validate its return type."""
    result = get_bench_comparator(jurisdiction)(*args, **kwargs)
    if not isinstance(result, BenchUnitResult):
        raise TypeError(
            f"comparator for {jurisdiction!r} returned {type(result).__name__}, "
            "expected BenchUnitResult"
        )
    return result
