"""uk_prefetch.py — Pre-fetch missing affecting act XMLs into the UK Farchive.

Shared library used by:
  - scripts/fetch_uk_affecting_acts.py  (batch / bench-corpus mode)
  - lawvm uk-fetch-affecting             (single-statute CLI)
  - lawvm uk-replay --fetch-missing      (inline pre-fetch before replay)
"""

from __future__ import annotations

import sys
import time
import urllib.error
import urllib.request
from typing import Any

_LEG_BASE = "https://www.legislation.gov.uk"
_USER_AGENT = "LawVM UK fetch/0.1 (+https://github.com/lawvm)"

# Minimum inter-request delay (seconds).
_MIN_DELAY = 0.5


def _missing_affecting_locator(act_id: str) -> str:
    """Negative-cache locator for permanently missing affecting act XML."""
    return f"leg://missing/uk/{act_id}/data.xml"


def fetch_missing_for_statute(
    sid: str,
    archive: Any,  # Farchive — avoid circular import at module level
    delay: float = 0.8,
    dry_run: bool = False,
    verbose: bool = False,
) -> tuple[int, int, int]:
    """Fetch affecting act XMLs that are referenced by *sid*'s effects but not yet cached.

    Loads effects from the archive, deduplicates by affecting act ID, checks
    the archive cache, and HTTP-fetches anything missing.

    Args:
        sid:      Statute ID in web-path form, e.g. ``ukpga/1998/42``.
        archive:  Open :class:`farchive.Farchive` instance.
        delay:    Seconds to sleep between HTTP requests (clamped to ``_MIN_DELAY``).
        dry_run:  Print what would be fetched but do not download.
        verbose:  Print a line for every affecting act checked.

    Returns:
        ``(fetched_count, already_cached_count, error_count)``
    """
    from lawvm.uk_legislation.uk_amendment_replay import (
        load_effects_for_statute_from_archive,
        get_affecting_act_xml_from_archive,
    )

    effects = load_effects_for_statute_from_archive(sid, archive)
    structural = [e for e in effects if e.is_structural]

    if not structural:
        if verbose:
            print(f"  {sid}: no structural effects in archive", file=sys.stderr)
        return 0, 0, 0

    effective_delay = max(delay, _MIN_DELAY)
    fetched = cached = errors = 0
    seen_acts: set[str] = set()

    for e in structural:
        act_id = e.affecting_act_id
        if act_id in seen_acts:
            continue
        seen_acts.add(act_id)
        missing_locator = _missing_affecting_locator(act_id)

        if archive.has(missing_locator):
            cached += 1
            if verbose:
                print(f"  MISSING  {act_id}", file=sys.stderr)
            continue

        # Check cache first — no network round-trip if already stored.
        xml = get_affecting_act_xml_from_archive(act_id, archive)
        if xml:
            cached += 1
            if verbose:
                print(f"  CACHED  {act_id}", file=sys.stderr)
            continue

        url = f"{_LEG_BASE}/{act_id}/data.xml"

        if dry_run:
            print(f"  DRY-RUN would fetch: {url}")
            fetched += 1  # count "would-fetch" as fetched for summary purposes
            continue

        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
            if len(data) < 100:
                print(
                    f"  WARN: suspiciously small response ({len(data)} bytes): {url}",
                    file=sys.stderr,
                )
                errors += 1
            else:
                archive.store(url, data, storage_class="xml")
                fetched += 1
                if verbose:
                    print(f"  FETCHED {act_id}  ({len(data):,} bytes)", file=sys.stderr)
        except urllib.error.HTTPError as exc:
            if exc.code in {404, 410}:
                archive.store(missing_locator, b"404", storage_class="text")
                cached += 1
                if verbose:
                    print(f"  HTTP {exc.code}: {act_id} (permanent miss)", file=sys.stderr)
                time.sleep(effective_delay)
                continue
            print(f"  HTTP {exc.code}: {url}", file=sys.stderr)
            errors += 1
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            print(f"  NETWORK ERROR: {url}: {exc}", file=sys.stderr)
            errors += 1

        time.sleep(effective_delay)

    return fetched, cached, errors
