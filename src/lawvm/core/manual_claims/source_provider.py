"""SourceBytesProvider — jurisdiction-agnostic source-bytes abstraction.

Piece 1 of the source-provider stack (see task spec §Piece 1).

The registry maps jurisdiction codes to per-jurisdiction SourceBytesProvider
implementations. `cmd_propose_claims` calls `get_source_provider(jurisdiction)`
then `provider.fetch(scope)` to obtain real source bytes for each frontier row.

No Finland-specific code here. No LLM-specific concepts.

AGENTS.md discipline:
  §1.9: no getattr/stringly-typed dispatch; typed Protocol + dict registry.
  §1.10: no broad try/except.
  Frozen dataclass + slots per project convention.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional, Protocol

from lawvm.core.manual_claims.primitive import ClaimScope, SourceLocator


@dataclass(frozen=True, slots=True)
class FetchedSource:
    """Resolved source bytes for one frontier row's cited span.

    bytes_:     Raw bytes of the relevant source artifact (e.g. section XML/text).
    sha256_hex: Full SHA-256 of bytes_.
    locator:    SourceLocator pointing at the artifact.
    span:       (start, end) byte offsets into bytes_ for the cited span.
                Section-granularity providers set span=(0, len(bytes_)).
    """

    bytes_: bytes
    sha256_hex: str
    locator: SourceLocator
    span: tuple[int, int]


class SourceBytesProvider(Protocol):
    """Fetches source bytes for a frontier row's cited span.

    Jurisdiction-agnostic interface. Per-jurisdiction implementations
    (FinlexSectionSourceProvider, future UKLegislationProvider) are
    registered by jurisdiction code at startup.
    """

    def fetch(self, scope: ClaimScope) -> Optional[FetchedSource]: ...


# ---------------------------------------------------------------------------
# Module-level registry
# ---------------------------------------------------------------------------

_PROVIDERS: dict[str, SourceBytesProvider] = {}


def register_source_provider(jurisdiction: str, provider: SourceBytesProvider) -> None:
    """Register *provider* for *jurisdiction*.

    Called at CLI startup before the first propose-claims invocation.
    Registering the same jurisdiction twice replaces the previous entry.
    """
    _PROVIDERS[jurisdiction] = provider


def get_source_provider(jurisdiction: str) -> SourceBytesProvider:
    """Return the registered provider for *jurisdiction*.

    Raises KeyError with a descriptive message if no provider is registered
    (caller should register providers at startup before calling this).
    """
    if jurisdiction not in _PROVIDERS:
        registered = sorted(_PROVIDERS.keys())
        raise KeyError(
            f"No SourceBytesProvider registered for jurisdiction {jurisdiction!r}. "
            f"Registered jurisdictions: {registered}. "
            "Register a provider via register_source_provider() at CLI startup."
        )
    return _PROVIDERS[jurisdiction]


def make_fetched_source(
    bytes_: bytes,
    locator: SourceLocator,
    *,
    span: Optional[tuple[int, int]] = None,
) -> FetchedSource:
    """Convenience constructor: computes sha256_hex and defaults span to full bytes."""
    if span is None:
        span = (0, len(bytes_))
    return FetchedSource(
        bytes_=bytes_,
        sha256_hex=hashlib.sha256(bytes_).hexdigest(),
        locator=locator,
        span=span,
    )


@dataclass(frozen=True, slots=True)
class MockSourceProvider:
    """Deterministic test provider. Returns a fixed bytes payload for any scope.

    canned_bytes:  always return this payload when set; returns None otherwise.

    Used by tests to inject a predictable source into propose-claims without
    a real corpus store.
    """

    canned_bytes: Optional[bytes] = b"lain 1234/2020 on voimassa"

    def fetch(self, scope: ClaimScope) -> Optional[FetchedSource]:
        if self.canned_bytes is None:
            return None
        locator = SourceLocator(
            artifact_kind="finlex_akn",
            statute_id=scope.statute_id,
            he_id=None,
            version_id=None,
        )
        return make_fetched_source(self.canned_bytes, locator)
