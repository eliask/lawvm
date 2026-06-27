"""StrictProfileRegistry — frontend-owned default StrictProfile registration.

This inverts the prior core→frontend import in
``compile_metadata_default._default_strict_profile`` (which did
``from lawvm.finland.strict_profile import default_finland_strict_profile``
— the only hard Python import from a frontend in ``core/``). Per AGENTS.md §2.3
the core must not interpret frontend-local hooks; frontends register
themselves here.

API tier
--------
Core primitive: a module-level dict keyed by jurisdiction code, plus
``register_default_strict_profile`` (called by frontend packages at import
time) and ``get_default_strict_profile`` (called by
``compile_metadata_default`` to look up the canonical profile).

The registry stores *factories* (``Callable[[], StrictProfile]``), not
instances, so a frontend's profile is constructed fresh per call (mirrors the
prior call-time ``default_finland_strict_profile()`` semantics). An unregistered
jurisdiction resolves to ``None`` — callers fall back to the generic
``StrictProfile(name="{jurisdiction}_default_v1")`` and stay fail-loud about
the gap rather than silently guessing a frontend's profile.

Frontend registration side-effects live in the frontend package — the canonical
site is ``finland/__init__.py``'s eager ``_register_default_strict_profile()``
module-scope call. Importing any ``lawvm.<frontend>.<submodule>`` triggers
``<frontend>/__init__.py`` evaluation and thus the registration; the core does
not import any frontend package.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    # ``StrictProfile`` lives in ``lawvm.core.compile_result``; imported under
    # TYPE_CHECKING only to avoid importing that module at registry-import time
    # (callers like ``compile_metadata_default`` already import it).
    from lawvm.core.compile_result import StrictProfile


# Module-level jurisdiction → factory table. Populated by each frontend at
# package import time via ``register_default_strict_profile``. Registering
# twice for a jurisdiction overwrites — a frontend owns its key (mirrors the
# bench_comparator_registry / source_provider registry pattern).
_FACTORIES: dict[str, Callable[[], "StrictProfile"]] = {}


def register_default_strict_profile(
    jurisdiction: str,
    factory: Callable[[], "StrictProfile"],
) -> None:
    """Register *factory* as the canonical StrictProfile builder for *jurisdiction*.

    Called at frontend package import time (e.g. ``finland/__init__.py``
    registers ``default_finland_strict_profile`` for ``"fi"``). Registering
    twice overwrites — the frontend owns its jurisdiction key.

    Args:
        jurisdiction: Jurisdiction code (``"fi"``, ``"ee"``, ``"uk"``, ...).
            Must be a non-empty string.
        factory: Zero-arg callable returning a fresh ``StrictProfile`` instance
            each call (mirrors the prior ``default_finland_strict_profile()``
            per-call construction semantics).

    Raises:
        ValueError: if *jurisdiction* is empty or *factory* is not callable.
    """
    if not jurisdiction or not isinstance(jurisdiction, str):
        raise ValueError(
            "register_default_strict_profile requires a non-empty jurisdiction string"
        )
    if not callable(factory):
        raise ValueError(
            f"register_default_strict_profile({jurisdiction!r}): factory must be callable, "
            f"got {type(factory).__name__}"
        )
    _FACTORIES[jurisdiction] = factory


def get_default_strict_profile(
    jurisdiction: str,
) -> Optional["StrictProfile"]:
    """Return the canonical ``StrictProfile`` for *jurisdiction*, or ``None``.

    Looks up the registered factory and invokes it (per-call construction
    semantics — the returned profile is a fresh instance, owned by the caller).
    Returns ``None`` when no factory is registered for *jurisdiction* — callers
    fall back to the generic ``StrictProfile(name="{jurisdiction}_default_v1")``
    in that case. No Python import is attempted here: if a frontend's package
    has not been imported by the caller, the registration side-effect will not
    have fired and this function returns ``None`` (callers that need the
    canonical profile must ensure the frontend package is loaded, which is the
    standing contract for any frontend-typed behaviour).
    """
    factory = _FACTORIES.get(jurisdiction)
    if factory is None:
        return None
    return factory()


def get_default_strict_profile_factory(
    jurisdiction: str,
) -> Optional[Callable[[], "StrictProfile"]]:
    """Return the registered factory callable for *jurisdiction*, or ``None``.

    Exposed for tests that want to assert that a frontend registration landed
    without invoking the factory (e.g. to assert it is the original
    ``default_finland_strict_profile`` reference).
    """
    return _FACTORIES.get(jurisdiction)


def registered_strict_profile_jurisdictions() -> tuple[str, ...]:
    """Return the sorted tuple of jurisdictions with a registered factory.

    Mirrors ``bench_comparator_registry.registered_jurisdictions()``: a
    completeness gate can pin that every jurisdiction shipping a strict profile
    is registered here.
    """
    return tuple(sorted(_FACTORIES))


def _clear_registry_for_tests() -> None:
    """Drop every registered factory. Test-only — never call from production.

    Used by registry unit tests to isolate themselves from the wide
    ``finland`` package import side-effect that pre-populates ``"fi"``.
    """
    _FACTORIES.clear()


__all__ = [
    "get_default_strict_profile",
    "get_default_strict_profile_factory",
    "registered_strict_profile_jurisdictions",
    "register_default_strict_profile",
]
