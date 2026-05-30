"""uk_acquire.py — Per-statute UK legislation acquisition helpers.

Shared library used by:
  - lawvm uk-acquire              (single-statute CLI)
  - scripts/acquire_uk_corpus.py  (full-corpus batch, via compatible logic)

Handles:
  - Fetching enacted XML for a statute (immutable, stored once).
  - Fetching current XML for a statute (slow-mutable, TTL-governed).
  - Fetching effects feed pages for a statute (slow-mutable, TTL-governed).

Rate limiting and retry are handled by the caller or by the simple internal
HTTP helpers here.  For full-corpus acquisition, ``scripts/acquire_uk_corpus.py``
has its own rate-limiter — this module is tuned for single-statute interactive use.
"""

from __future__ import annotations

import hashlib
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_LEG_BASE = "https://www.legislation.gov.uk"
_USER_AGENT = "LawVM/1.0 (+https://github.com/lawvm)"

# Default inter-request delay for interactive/single-statute use.
_DEFAULT_DELAY = 0.5

# TTLs (seconds) for mutable resources.
_TTL_CURRENT = 300 * 86400  # ~30 days
_TTL_EFFECTS = 300 * 86400  # ~30 days

_DEFAULT_ARCHIVE_PATH = Path(__file__).resolve().parents[4] / "data" / "uk_legislation.farchive"


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class UKAcquirePlan:
    """Dry-run plan: what would be fetched for a single statute."""

    statute_id: str
    enacted_url: str
    enacted_already_cached: bool
    current_url: str
    current_stale: bool
    effects_base_url: str
    effects_stale: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "statute_id": self.statute_id,
            "enacted_url": self.enacted_url,
            "enacted_already_cached": self.enacted_already_cached,
            "current_url": self.current_url,
            "current_stale": self.current_stale,
            "effects_base_url": self.effects_base_url,
            "effects_stale": self.effects_stale,
        }

    def would_fetch(self) -> list[str]:
        """Return list of URLs that would be fetched (not yet cached or stale)."""
        urls: list[str] = []
        if not self.enacted_already_cached:
            urls.append(self.enacted_url)
        if self.current_stale:
            urls.append(self.current_url)
        if self.effects_stale:
            urls.append(self.effects_base_url)
        return urls


@dataclass
class UKAcquireReport:
    """Machine-readable result of a uk-acquire run."""

    statute_id: str
    enacted_fetched: bool = False
    enacted_already_cached: bool = False
    enacted_error: str | None = None
    current_fetched: bool = False
    current_already_cached: bool = False
    current_error: str | None = None
    effects_pages_fetched: int = 0
    effects_already_cached: bool = False
    effects_error: str | None = None
    affecting_fetched: int = 0
    affecting_cached: int = 0
    affecting_errors: int = 0
    affecting_events: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "statute_id": self.statute_id,
            "enacted_fetched": self.enacted_fetched,
            "enacted_already_cached": self.enacted_already_cached,
            "current_fetched": self.current_fetched,
            "current_already_cached": self.current_already_cached,
            "effects_pages_fetched": self.effects_pages_fetched,
            "effects_already_cached": self.effects_already_cached,
            "affecting_fetched": self.affecting_fetched,
            "affecting_cached": self.affecting_cached,
            "affecting_errors": self.affecting_errors,
        }
        if self.enacted_error:
            d["enacted_error"] = self.enacted_error
        if self.current_error:
            d["current_error"] = self.current_error
        if self.effects_error:
            d["effects_error"] = self.effects_error
        if self.affecting_events:
            d["affecting_events"] = self.affecting_events
        return d

    @property
    def has_errors(self) -> bool:
        return bool(self.enacted_error or self.current_error or self.effects_error or self.affecting_errors)


# ---------------------------------------------------------------------------
# Internal HTTP helpers
# ---------------------------------------------------------------------------


def _http_get(url: str, delay: float = _DEFAULT_DELAY, last_time: list[float] | None = None) -> tuple[bytes | None, int | None]:
    """Fetch URL with minimal rate-limiting.

    Returns ``(data, status_code)`` where data is None on failure.
    ``last_time`` is a one-element list used as a mutable counter for delay.
    """
    if last_time is not None and last_time:
        elapsed = time.monotonic() - last_time[0]
        if elapsed < delay:
            time.sleep(delay - elapsed)

    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
            if last_time is not None:
                last_time[:] = [time.monotonic()]
            return data, getattr(resp, "status", 200)
    except urllib.error.HTTPError as exc:
        if last_time is not None:
            last_time[:] = [time.monotonic()]
        return None, exc.code
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        if last_time is not None:
            last_time[:] = [time.monotonic()]
        return None, None


def _is_stale(archive: Any, url: str, ttl: float) -> bool:
    """Return True if locator is missing or last_confirmed_at > ttl seconds ago."""
    spans = archive.history(url)
    if not spans:
        return True
    last = spans[-1].last_confirmed_at
    if last is None:
        return True
    return (time.time() - last.timestamp()) > ttl


def _store_if_new(archive: Any, url: str, data: bytes, sc: str = "xml") -> bool:
    """Store only if content digest differs from what is already stored."""
    spans = archive.history(url)
    if spans:
        digest = hashlib.sha256(data).hexdigest()
        if spans[-1].digest == digest:
            return False
    archive.store(url, data, storage_class=sc)
    return True


def _parse_statute_id(statute_id: str) -> tuple[str, str, str]:
    """Parse 'act_type/year/number' -> (act_type, year, number)."""
    parts = statute_id.strip("/").split("/")
    if len(parts) != 3 or not all(parts):
        raise ValueError(f"invalid UK statute id: {statute_id!r}  (expected act_type/year/number, e.g. ukpga/2020/17)")
    return parts[0], parts[1], parts[2]


# ---------------------------------------------------------------------------
# Per-statute acquisition logic
# ---------------------------------------------------------------------------


def build_acquire_plan(statute_id: str, archive: Any) -> UKAcquirePlan:
    """Build a dry-run plan showing what would be fetched for *statute_id*.

    Does NOT make any network requests.
    """
    act_type, year, number = _parse_statute_id(statute_id)
    base = f"{_LEG_BASE}/{act_type}/{year}/{number}"
    enacted_url = f"{base}/enacted/data.xml"
    current_url = f"{base}/data.xml"
    effects_base_url = f"{_LEG_BASE}/changes/affected/{act_type}/{year}/{number}/data.feed?results-count=50&sort=modified"

    enacted_cached = archive.has(enacted_url)
    current_stale = _is_stale(archive, current_url, _TTL_CURRENT)
    effects_stale = _is_stale(archive, effects_base_url, _TTL_EFFECTS)

    return UKAcquirePlan(
        statute_id=statute_id,
        enacted_url=enacted_url,
        enacted_already_cached=enacted_cached,
        current_url=current_url,
        current_stale=current_stale,
        effects_base_url=effects_base_url,
        effects_stale=effects_stale,
    )


def _fetch_effects_pages_for_statute(
    act_type: str,
    year: str,
    number: str,
    archive: Any,
    delay: float,
    timer: list[float],
    *,
    force: bool = False,
) -> tuple[int, str | None]:
    """Fetch effects feed pages for one statute.

    Returns ``(pages_fetched, error_message_or_None)``.
    """
    base = f"{_LEG_BASE}/changes/affected/{act_type}/{year}/{number}/data.feed"
    p1_url = f"{base}?results-count=50&sort=modified"

    if not force and not _is_stale(archive, p1_url, _TTL_EFFECTS):
        return 0, None  # already fresh

    data, status = _http_get(p1_url, delay=delay, last_time=timer)
    if not data:
        return 0, f"http_{status}" if status else "transport_error"

    _store_if_new(archive, p1_url, data, "xml")
    pages_fetched = 1

    total_pages = 1
    try:
        root = ET.fromstring(data)
        el = root.find(".//{http://www.legislation.gov.uk/namespaces/legislation}totalPages")
        total_pages = int(el.text) if el is not None and el.text else 1
    except ET.ParseError:
        pass

    for p in range(2, total_pages + 1):
        purl = f"{p1_url}&page={p}"
        if not force and not _is_stale(archive, purl, _TTL_EFFECTS):
            continue
        pdata, pstatus = _http_get(purl, delay=delay, last_time=timer)
        if pdata:
            _store_if_new(archive, purl, pdata, "xml")
            pages_fetched += 1

    return pages_fetched, None


def acquire_statute(
    statute_id: str,
    archive: Any,
    *,
    enacted_only: bool = False,
    affecting: bool = False,
    force_refresh: bool = False,
    delay: float = _DEFAULT_DELAY,
    verbose: bool = False,
) -> UKAcquireReport:
    """Download enacted XML, current XML, and effects feed pages for *statute_id*.

    Args:
        statute_id:     UK statute ID, e.g. ``ukpga/2020/17``.
        archive:        Open :class:`farchive.Farchive` instance.
        enacted_only:   Only fetch enacted XML; skip current + effects.
        affecting:      Also run affecting-act prefetch after primary fetch.
        force_refresh:  Re-fetch mutable resources even if TTL says fresh.
        delay:          Seconds between HTTP requests.
        verbose:        Print progress lines to stdout.
    """
    act_type, year, number = _parse_statute_id(statute_id)
    base = f"{_LEG_BASE}/{act_type}/{year}/{number}"
    enacted_url = f"{base}/enacted/data.xml"
    current_url = f"{base}/data.xml"

    report = UKAcquireReport(statute_id=statute_id)
    timer: list[float] = [0.0]

    # --- Enacted XML (immutable: store once) ---
    if archive.has(enacted_url):
        report.enacted_already_cached = True
        if verbose:
            print(f"  enacted: cached  {enacted_url}")
    else:
        data, status = _http_get(enacted_url, delay=delay, last_time=timer)
        if data and len(data) > 50:
            archive.store(enacted_url, data, storage_class="xml")
            report.enacted_fetched = True
            if verbose:
                print(f"  enacted: fetched  {len(data):,} bytes  {enacted_url}")
        else:
            report.enacted_error = f"http_{status}" if status else "transport_error"
            if verbose:
                print(f"  enacted: ERROR {report.enacted_error}  {enacted_url}")

    if enacted_only:
        return report

    # --- Current XML (slow-mutable: TTL-governed) ---
    if not force_refresh and not _is_stale(archive, current_url, _TTL_CURRENT):
        report.current_already_cached = True
        if verbose:
            print(f"  current: cached  {current_url}")
    else:
        data, status = _http_get(current_url, delay=delay, last_time=timer)
        if data and len(data) > 50:
            _store_if_new(archive, current_url, data, "xml")
            report.current_fetched = True
            if verbose:
                print(f"  current: fetched  {len(data):,} bytes  {current_url}")
        else:
            report.current_error = f"http_{status}" if status else "transport_error"
            if verbose:
                print(f"  current: ERROR {report.current_error}  {current_url}")

    # --- Effects feed pages (slow-mutable: TTL-governed) ---
    pages_fetched, err = _fetch_effects_pages_for_statute(
        act_type,
        year,
        number,
        archive,
        delay=delay,
        timer=timer,
        force=force_refresh,
    )
    if err:
        report.effects_error = err
        if verbose:
            print(f"  effects: ERROR {err}")
    elif pages_fetched == 0:
        report.effects_already_cached = True
        if verbose:
            print("  effects: cached")
    else:
        report.effects_pages_fetched = pages_fetched
        if verbose:
            print(f"  effects: fetched {pages_fetched} page(s)")

    # --- Affecting acts (optional) ---
    if affecting:
        from lawvm.uk_legislation.uk_prefetch import fetch_missing_for_statute

        prefetch = fetch_missing_for_statute(
            statute_id,
            archive,
            delay=delay,
            dry_run=False,
            verbose=verbose,
        )
        report.affecting_fetched = prefetch.fetched_count
        report.affecting_cached = prefetch.already_cached_count
        report.affecting_errors = prefetch.error_count
        report.affecting_events = list(prefetch.events)

    return report
