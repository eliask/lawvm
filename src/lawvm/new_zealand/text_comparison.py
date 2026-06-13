"""New Zealand comparison-only text normalization helpers.

These helpers are for witness/oracle comparison only. They must not be used to
repair source XML, replay payloads, or legal tree state.

The normalization rules and inline occurrence logic now live in
``lawvm.core.comparison_normalization`` as the jurisdiction-neutral default for
Westminster / common-law frontends. The NZ-named functions below delegate to the
shared core so there is a single source of truth; they keep their existing
signatures and outputs.
"""

from __future__ import annotations

from lawvm.core.comparison_normalization import (
    normalize_inline_comparison_text,
    normalized_inline_contains,
    normalized_inline_occurrence_count,
)

__all__ = [
    "normalize_nz_inline_comparison_text",
    "normalized_nz_inline_occurrence_count",
    "normalized_nz_inline_contains",
]


def normalize_nz_inline_comparison_text(text: str) -> str:
    return normalize_inline_comparison_text(text)


def normalized_nz_inline_occurrence_count(haystack: str, needle: str) -> int:
    return normalized_inline_occurrence_count(haystack, needle)


def normalized_nz_inline_contains(haystack: str, needle: str) -> bool:
    return normalized_inline_contains(haystack, needle)
