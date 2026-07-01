"""Run-scoped parse/archive cache for NZ dry-run corpus + north-star runners.

The per-work dry-run surfaces (operation surface, candidate preflight, the repeal
and text_replace kernels) each open the farchive and parse the same archived XML
versions independently. Across a whole corpus run the *same* archived version XML
is therefore decompressed and parsed many times: once per family, per work, and
again for the change-window lookups. That redundant lxml parsing dominates the
corpus and north-star wall clock.

This module provides an opt-in, run-scoped cache that makes each archived version
XML parse at most once and reuses one opened archive handle per path for the whole
run. It is purely a performance layer:

- It is OFF by default. With no active cache (the normal single-work path), every
  hooked call behaves exactly as before — same archive opens, same parses.
- When a runner enters :func:`corpus_run_cache`, the hooks in
  :func:`lawvm.new_zealand.acquisition.open_farchive` and
  :func:`lawvm.new_zealand.source_tree.parse_nz_source_document` consult the
  active cache. The cache keys parsed documents by ``(xml_locator, version_id)``
  and the archive handle by its resolved filesystem path plus read/write mode. Parsed
  :class:`NZSourceDocument` values are frozen and built from immutable tuples, so
  sharing one instance across consumers cannot change any result.

Determinism: the cache only memoizes pure, input-addressed values. A cache hit
returns the byte-identical object a cache miss would have computed, so the
runner's output is byte-identical to the uncached serial path. No clock, no
randomness, no ordering dependence.
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

if TYPE_CHECKING:
    from lawvm.new_zealand.dependencies import NZLatestXMLLocatorSelection
    from lawvm.new_zealand.source_tree import NZSourceDocument


class _SharedArchive:
    """Thin wrapper that shares one opened archive across a run.

    Delegates every attribute to the real archive but:

    - turns ``close()`` into a no-op so a per-work ``try/finally: archive.close()``
      does not close the run-shared handle (the owning cache closes the real
      archive when the run context exits);
    - memoizes the read-only ``locators(pattern)`` SQL scan, which the per-work
      surfaces issue many times against the same prefix. The runners never mutate
      the archive (measurement only), so a pattern's locator list is stable for
      the whole run. This is byte-identical to re-querying and is the dominant
      remaining cost once XML parsing is cached;
    - memoizes ``get(locator)`` (default ``at=None``) bytes. Byte payloads can be
      large, so this memo is cleared per-work by the owning cache's
      ``reset_parsed`` while the locator-list memo persists run-wide.

    Both memoized methods fall through to the real archive for any non-default
    call shape, so no behavior changes.
    """

    __slots__ = ("_archive", "_cache_key", "_locators_cache", "_bytes_cache")

    def __init__(self, archive: Any, *, cache_key: str) -> None:
        self._archive = archive
        self._cache_key = cache_key
        self._locators_cache: dict[str, list[str]] = {}
        self._bytes_cache: dict[str, bytes | None] = {}

    def close(self) -> None:  # shared handle: closed by the owning cache, not here
        return None

    def locators(self, pattern: str = "%") -> list[str]:
        cached = self._locators_cache.get(pattern)
        if cached is not None:
            # Return a copy so a caller mutating the list cannot corrupt the memo
            # (the real method returns a fresh list each call).
            return list(cached)
        result = self._archive.locators(pattern)
        self._locators_cache[pattern] = list(result)
        return result

    def get(self, locator: str, *, at: Any = None) -> bytes | None:
        if at is not None:
            return self._archive.get(locator, at=at)
        if locator in self._bytes_cache:
            return self._bytes_cache[locator]
        data = self._archive.get(locator)
        self._bytes_cache[locator] = data
        return data

    def _clear_per_work(self) -> None:
        # The within-work redundancy is the same work prefix queried many times
        # and the same locator fetched many times; different works use disjoint
        # prefixes/locators, so retaining either across works is pure memory bloat
        # with no cache benefit. The owning cache clears both between works.
        self._bytes_cache.clear()
        self._locators_cache.clear()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._archive, name)


class CorpusRunCache:
    """Holds run-scoped shared archives and memoized parsed documents."""

    __slots__ = ("_archives", "_latest_selections", "_parsed")

    def __init__(self) -> None:
        # (resolved archive path, readonly) -> (real archive, shared wrapper).
        self._archives: dict[tuple[str, bool], tuple[Any, _SharedArchive]] = {}
        # (archive identity, work_id) -> latest archived XML selection.
        self._latest_selections: dict[tuple[object, str], NZLatestXMLLocatorSelection] = {}
        # (xml_locator, version_id) -> parsed document (pure, input-addressed).
        self._parsed: dict[tuple[str, str], NZSourceDocument] = {}

    def open_archive(self, path: Path, opener: Any, *, readonly: bool = True) -> Any:
        key = (self._archive_key(path), readonly)
        existing = self._archives.get(key)
        if existing is not None:
            return existing[1]
        archive_key = self._archive_key(path)
        real = opener(path)
        shared = _SharedArchive(real, cache_key=archive_key)
        self._archives[key] = (real, shared)
        return shared

    def latest_locator_selection(
        self,
        archive: Any,
        work_id: str,
        selector: Any,
    ) -> "NZLatestXMLLocatorSelection":
        """Memoize source-inventory selection during one active NZ run.

        The selector reads only version-detail JSON, XML locators, and candidate
        diagnostics. Caching its typed result avoids repeated archive inventory
        scans without retaining parsed XML roots or source bytes.
        """

        key = (getattr(archive, "_cache_key", id(archive)), work_id)
        cached = self._latest_selections.get(key)
        if cached is not None:
            return cached
        selection = selector(archive, work_id)
        self._latest_selections[key] = selection
        return selection

    def parse_document(
        self,
        xml_bytes: bytes,
        *,
        xml_locator: str,
        version_id: str,
        parser: Any,
    ) -> NZSourceDocument:
        # Only memoize when the locator identifies the bytes: the archive is
        # content-addressed by locator, so (xml_locator, version_id) uniquely
        # determines the parse input. An empty locator is not an identity, so we
        # never cache it (it would risk collapsing distinct byte payloads).
        if not xml_locator:
            return parser(xml_bytes, xml_locator=xml_locator, version_id=version_id)
        key = (xml_locator, version_id)
        cached = self._parsed.get(key)
        if cached is not None:
            return cached
        document = parser(xml_bytes, xml_locator=xml_locator, version_id=version_id)
        self._parsed[key] = document
        return document

    def reset_parsed(self) -> None:
        """Drop memoized parsed documents, keeping shared archive handles.

        The dominant redundancy is *within* one work: the operation surface,
        candidate preflight, and both family kernels each parse the same archived
        latest/before/oracle versions and ask for the same latest dependency
        locators. Different works share almost no archived versions or dependency
        inventory selections, so retaining either across works would only grow
        memory without a cache benefit. The corpus/north-star runners call this
        between works to bound the working set while keeping the within-work
        reuse and the run-shared archive handle.
        """

        self._parsed.clear()
        self._latest_selections.clear()
        for _real, shared in self._archives.values():
            shared._clear_per_work()

    def close(self) -> None:
        for real, _shared in self._archives.values():
            real.close()
        self._archives.clear()
        self._latest_selections.clear()
        self._parsed.clear()

    @staticmethod
    def _archive_key(path: Path) -> str:
        try:
            return str(Path(path).resolve())
        except OSError:
            return str(path)


_ACTIVE_CACHE: contextvars.ContextVar[CorpusRunCache | None] = contextvars.ContextVar(
    "nz_corpus_run_cache", default=None
)


def active_corpus_run_cache() -> CorpusRunCache | None:
    return _ACTIVE_CACHE.get()


@contextmanager
def corpus_run_cache() -> Iterator[CorpusRunCache]:
    """Activate a run-scoped parse/archive cache for the duration of the block.

    Re-entrant: a nested call reuses the already-active cache and does not close
    it on exit (the outermost block owns the lifecycle).
    """

    existing = _ACTIVE_CACHE.get()
    if existing is not None:
        yield existing
        return
    cache = CorpusRunCache()
    token = _ACTIVE_CACHE.set(cache)
    try:
        yield cache
    finally:
        _ACTIVE_CACHE.reset(token)
        cache.close()
