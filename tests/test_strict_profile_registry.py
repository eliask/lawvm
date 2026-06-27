"""Tests for ``lawvm.core.strict_profile_registry`` — the §2.3 inversion that
replaces the prior ``core → lawvm.finland.strict_profile`` import in
``compile_metadata_default._default_strict_profile`` with a frontend-owned
registration callback pattern.

The registry maps jurisdiction codes to ``Callable[[], StrictProfile]``
factories populated at frontend-package import time. ``compile_metadata_default``
then asks the registry for the canonical profile instead of hard-importing a
frontend.

Covers:
  1. ``register_default_strict_profile`` populates the registry; the
     factory is invoked per ``get`` call (fresh instances, not a cached one).
  2. Unknown jurisdiction returns ``None`` (no Python import attempted —
     the registry is a dict lookup, never a deferred import).
  3. A registered factory IS called when
     ``build_default_compile_metadata`` is exercised for the jurisdiction
     (the registry is the dispatch path used by ``_default_strict_profile``).
  4. End-to-end Finland regression: ``finland/__init__.py`` registers
     ``default_finland_strict_profile`` for ``"fi"``, and the registry's
     returned profile is the same ``finland_ingestion_v1`` profile as before
     the inversion.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable

import pytest

from lawvm.core.compile_result import StrictProfile
from lawvm.core.strict_profile_registry import (
    _clear_registry_for_tests,
    get_default_strict_profile,
    get_default_strict_profile_factory,
    registered_strict_profile_jurisdictions,
    register_default_strict_profile,
)

if TYPE_CHECKING:
    from lawvm.core.compile_metadata_default import _default_strict_profile  # noqa: F401


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def _isolated_registry():
    """Snapshot and restore the global registry so production registrations
    (e.g. the live ``"fi"`` entry from importing ``lawvm.finland``) are
    preserved across the suite while tests stage their own throwaway keys."""
    import lawvm.core.strict_profile_registry as mod

    snapshot = dict(mod._FACTORIES)
    _clear_registry_for_tests()
    try:
        yield mod._FACTORIES
    finally:
        mod._FACTORIES.clear()
        mod._FACTORIES.update(snapshot)


def _profile_factory(name: str) -> Callable[[], StrictProfile]:
    def _factory() -> StrictProfile:
        return StrictProfile(name=name)

    return _factory


# ---------------------------------------------------------------------------
# 1. register_default_strict_profile populates the registry; get invokes the
#    factory and returns a fresh StrictProfile each call.
# ---------------------------------------------------------------------------


class TestRegisterAndGet:
    def test_register_adds_factory_and_get_returns_profile(self, _isolated_registry) -> None:
        factory = _profile_factory("test_register_v1")
        register_default_strict_profile("testjuris", factory)

        assert "testjuris" in registered_strict_profile_jurisdictions()

        profile = get_default_strict_profile("testjuris")
        assert profile is not None
        assert isinstance(profile, StrictProfile)
        assert profile.name == "test_register_v1"

        # The stored factory is the same callable we registered (no wrapping).
        assert get_default_strict_profile_factory("testjuris") is factory

    def test_factory_is_invoked_per_get_not_cached(self, _isolated_registry) -> None:
        calls: list[int] = []

        def _factory() -> StrictProfile:
            calls.append(1)
            return StrictProfile(name="freshness_probe")

        register_default_strict_profile("freshjuris", _factory)

        first = get_default_strict_profile("freshjuris")
        second = get_default_strict_profile("freshjuris")
        assert first is not None and second is not None
        # Distinct StrictProfile instances per call (frozen dataclasses compare
        # by value, so assert identity explicitly).
        assert first is not second
        assert first == second
        assert len(calls) == 2

    def test_register_rejects_empty_jurisdiction(self, _isolated_registry) -> None:
        with pytest.raises(ValueError, match="non-empty jurisdiction"):
            register_default_strict_profile("", _profile_factory("x"))

    def test_register_rejects_non_callable_factory(self, _isolated_registry) -> None:
        with pytest.raises(ValueError, match="factory must be callable"):
            register_default_strict_profile("nojen", object())  # ty: ignore[invalid-argument-type]

    def test_registering_twice_overwrites(self, _isolated_registry) -> None:
        register_default_strict_profile("doublejuris", _profile_factory("v1"))
        register_default_strict_profile("doublejuris", _profile_factory("v2"))
        profile = get_default_strict_profile("doublejuris")
        assert profile is not None
        assert profile.name == "v2"

    def test_registered_jurisdictions_returns_sorted_tuple(self, _isolated_registry) -> None:
        register_default_strict_profile("zzz", _profile_factory("z"))
        register_default_strict_profile("aaa", _profile_factory("a"))
        assert registered_strict_profile_jurisdictions() == ("aaa", "zzz")


# ---------------------------------------------------------------------------
# 2. Unknown jurisdiction returns None — no import attempted.
# ---------------------------------------------------------------------------


class TestUnknownJurisdiction:
    def testUnknownJurisdictionReturnsNone(self, _isolated_registry) -> None:
        # Registry is empty (isolated); any jurisdiction string returns None.
        assert get_default_strict_profile("fi") is None
        assert get_default_strict_profile("") is None  # never registered
        assert get_default_strict_profile_factory("nonexistent") is None

    def test_no_python_import_attempted_for_unknown_jurisdiction(
        self, _isolated_registry
    ) -> None:
        """A ``KeyError``-shaped miss must not surface as an import attempt.

        The prior core→frontend import hard-coupled the registry to a Python
        import (and raised ``ImportError`` when the frontend package was not
        installed). The new registry must be pure dict-lookup — an unknown
        jurisdiction returns ``None``, not an ``ImportError``. Guard-liveness:
        we point a non-existent jurisdiction at the registry and assert ``None``
        (not an ``ImportError``). If a future regression re-introduces a
        deferred ``importlib.import_module("lawvm." + jurisdiction)`` call,
        the resolver raises ``ModuleNotFoundError`` here instead.
        """
        # No monkey-patching needed: a pure dict lookup cannot import. If a
        # future regression tries `import lawvm.totally_unknown_jurisdiction`
        # to resolve the factory, Python raises ModuleNotFoundError right here
        # rather than returning None — the assertion fails loud.
        result = get_default_strict_profile("totally_unknown_jurisdiction_no_such_pkg")
        assert result is None


# ---------------------------------------------------------------------------
# 3. A registered factory is called when compile_metadata_default asks for it.
# ---------------------------------------------------------------------------


class TestCompileMetadataDispatchesToRegistry:
    def test_registered_factory_is_called_by_compile_metadata_default(
        self, _isolated_registry, tmp_path: Path
    ) -> None:
        """``build_default_compile_metadata(jurisdiction=X)`` invokes the
        factory registered for X and uses the returned profile's fingerprint,
        not the generic ``X_default_v1`` fallback."""
        from lawvm.core.compile_metadata import compute_strict_profile_fingerprint
        from lawvm.core.compile_metadata_default import build_default_compile_metadata

        sentinel_profile = StrictProfile(name="sentinel_for_dispatch_test")
        expected_fp = compute_strict_profile_fingerprint(sentinel_profile)

        register_default_strict_profile(
            "sentjuris", lambda: StrictProfile(name="sentinel_for_dispatch_test")
        )

        meta = build_default_compile_metadata(
            jurisdiction="sentjuris",
            source_bundle_hash="sha256:" + "a" * 64,
            build_id="test.dispatch",
            graph_store_root=tmp_path,  # empty dir — no policy/snapshot files
        )

        assert meta.strict_profile_fingerprint == expected_fp

    def test_unregistered_jurisdiction_uses_generic_fallback(
        self, _isolated_registry, tmp_path: Path
    ) -> None:
        """When no factory is registered, the generic fallback applies (and
        is distinguishable from the sentinel — no silent guessing)."""
        from lawvm.core.compile_metadata import compute_strict_profile_fingerprint
        from lawvm.core.compile_metadata_default import build_default_compile_metadata

        meta = build_default_compile_metadata(
            jurisdiction="unreg",
            source_bundle_hash="sha256:" + "b" * 64,
            build_id="test.generic",
            graph_store_root=tmp_path,
        )
        expected_fp = compute_strict_profile_fingerprint(
            StrictProfile(name="unreg_default_v1")
        )
        assert meta.strict_profile_fingerprint == expected_fp


# ---------------------------------------------------------------------------
# 4. End-to-end Finland regression — finland/__init__.py registers default.
# ---------------------------------------------------------------------------


class TestFinlandRegistrationEndToEnd:
    """Per AGENTS.md §2.3, importing a frontend package must register its
    canonical profile. ``import lawvm.finland`` triggers
    ``finland/__init__.py`` which calls ``register_default_strict_profile``.
    """

    def test_importing_finland_registers_fi_factory(self) -> None:
        # Importing fresh to trigger registration side-effect
        import lawvm.finland  # noqa: F401  (registration side-effect)

        assert "fi" in registered_strict_profile_jurisdictions()

        factory = get_default_strict_profile_factory("fi")
        assert factory is not None
        assert callable(factory)

        profile = get_default_strict_profile("fi")
        assert profile is not None
        assert profile.name == "finland_ingestion_v1"

    def test_fi_resolves_to_default_finland_strict_profile_directly(self) -> None:
        """The factory registered for ``"fi"`` IS the ``default_finland_strict_profile``
        function — no wrapping, no renaming."""
        import lawvm.finland  # noqa: F401  (registration side-effect)
        from lawvm.finland.strict_profile import default_finland_strict_profile

        assert get_default_strict_profile_factory("fi") is default_finland_strict_profile

    def test_fi_resolves_end_to_end_via_compile_metadata_default(self, tmp_path: Path) -> None:
        """Fire-drill (AGENTS.md §2.9): the existing ``"fi"`` end-to-end path
        still resolves ``default_finland_strict_profile`` through the registry
        when the Finland package is in scope, and the fingerprint matches the
        pre-inversion behaviour."""
        import lawvm.finland  # noqa: F401  (registration side-effect)
        from lawvm.core.compile_metadata import compute_strict_profile_fingerprint
        from lawvm.core.compile_metadata_default import build_default_compile_metadata
        from lawvm.finland.strict_profile import default_finland_strict_profile

        meta = build_default_compile_metadata(
            jurisdiction="fi",
            source_bundle_hash="sha256:" + "c" * 64,
            build_id="test.fi.e2e",
            graph_store_root=tmp_path,  # empty dir — no policy/snapshot files
        )

        expected_fp = compute_strict_profile_fingerprint(default_finland_strict_profile())
        assert meta.strict_profile_fingerprint == expected_fp
