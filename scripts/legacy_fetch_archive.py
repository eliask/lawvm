"""Explicit failure shim for scripts that still need FetchArchive migration.

The old ``lawvm.fetch_archive`` module has been removed.  Scripts importing this
class are intentionally not silently wired to Farchive because the old API mixed
HTTP fetching, freshness checks, SQLite internals, and decompression helpers.
Each caller needs a small, phase-owned migration to the current corpus/farchive
surface before it can be safely run again.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


class FetchArchive:
    """Removed legacy archive API.

    This class exists so obsolete scripts fail with a clear runtime message
    without carrying unresolved-import suppressions through the type checker.
    """

    _conn: Any

    def __init__(self, db_path: str | Path) -> None:
        raise RuntimeError(
            "lawvm.fetch_archive has been removed; migrate this script to the "
            f"current farchive/corpus API before using legacy archive {db_path!s}."
        )

    def get_latest(self, url: str) -> bytes | None:
        raise RuntimeError("FetchArchive.get_latest is unavailable; migrate this script.")

    def fetch(self, url: str, *, max_age_hours: float) -> bytes | None:
        raise RuntimeError("FetchArchive.fetch is unavailable; migrate this script.")

    def get_content(self, content_hash: str) -> bytes | None:
        raise RuntimeError("FetchArchive.get_content is unavailable; migrate this script.")

    def is_fresh(self, locator: str, max_age_hours: float) -> bool:
        raise RuntimeError("FetchArchive.is_fresh is unavailable; migrate this script.")

    def store(self, locator: str, data: bytes, *, content_type: str) -> None:
        raise RuntimeError("FetchArchive.store is unavailable; migrate this script.")

    def stats(self) -> dict[str, Any]:
        raise RuntimeError("FetchArchive.stats is unavailable; migrate this script.")

    def close(self) -> None:
        return None
