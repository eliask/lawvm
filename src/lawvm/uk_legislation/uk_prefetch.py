"""uk_prefetch.py — Pre-fetch missing affecting act XMLs into the UK Farchive.

Shared library used by:
  - scripts/fetch_uk_affecting_acts.py  (batch / bench-corpus mode)
  - lawvm uk-fetch-affecting             (single-statute CLI)
  - lawvm uk-replay --fetch-missing      (inline pre-fetch before replay)
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import sys
import time
import urllib.error
import urllib.request
from typing import Any

_LEG_BASE = "https://www.legislation.gov.uk"
_USER_AGENT = "LawVM UK fetch/0.1 (+https://github.com/lawvm)"

# Minimum inter-request delay (seconds).
_MIN_DELAY = 0.5


@dataclass(frozen=True)
class UKPrefetchReport:
    """Machine-readable UK affecting-act acquisition report.

    The report remains tuple-unpackable as ``(fetched, cached, errors)`` for
    existing callers, while preserving event rows for acquisition failures and
    known-missing source artifacts.
    """

    fetched_count: int
    already_cached_count: int
    error_count: int
    events: tuple[dict[str, Any], ...] = ()

    def __iter__(self) -> Iterator[int]:
        yield self.fetched_count
        yield self.already_cached_count
        yield self.error_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "fetched_count": self.fetched_count,
            "already_cached_count": self.already_cached_count,
            "error_count": self.error_count,
            "events": [dict(event) for event in self.events],
        }


def _missing_affecting_locator(act_id: str) -> str:
    """Negative-cache locator for permanently missing affecting act XML."""
    return f"leg://missing/uk/{act_id}/data.xml"


def _prefetch_event(
    *,
    statute_id: str,
    affecting_act_id: str,
    url: str,
    status: str,
    rule_id: str,
    reason: str,
    blocking: bool,
) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "phase": "acquisition",
        "family": "source_pathology",
        "statute_id": statute_id,
        "affecting_act_id": affecting_act_id,
        "locator": _missing_affecting_locator(affecting_act_id),
        "url": url,
        "status": status,
        "reason": reason,
        "blocking": blocking,
        "strict_disposition": "block" if blocking else "record",
        "quirks_disposition": "record",
    }


def fetch_missing_for_statute(
    sid: str,
    archive: Any,  # Farchive — avoid circular import at module level
    delay: float = 0.8,
    dry_run: bool = False,
    verbose: bool = False,
) -> UKPrefetchReport:
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
        A tuple-unpackable :class:`UKPrefetchReport`.
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
        return UKPrefetchReport(0, 0, 0)

    effective_delay = max(delay, _MIN_DELAY)
    fetched = cached = errors = 0
    seen_acts: set[str] = set()
    events: list[dict[str, Any]] = []

    for e in structural:
        act_id = e.affecting_act_id
        if act_id in seen_acts:
            continue
        seen_acts.add(act_id)
        missing_locator = _missing_affecting_locator(act_id)

        if archive.has(missing_locator):
            cached += 1
            events.append(
                _prefetch_event(
                    statute_id=sid,
                    affecting_act_id=act_id,
                    url=f"{_LEG_BASE}/{act_id}/data.xml",
                    status="skipped_cached_permanent_missing",
                    rule_id="uk_prefetch_permanent_missing_marker_skipped",
                    reason="permanent_missing_marker_cached",
                    blocking=False,
                )
            )
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
                events.append(
                    _prefetch_event(
                        statute_id=sid,
                        affecting_act_id=act_id,
                        url=url,
                        status="error",
                        rule_id="uk_prefetch_suspicious_small_response",
                        reason=f"suspiciously_small_response:{len(data)}",
                        blocking=True,
                    )
                )
            else:
                archive.store(url, data, storage_class="xml")
                fetched += 1
                if verbose:
                    print(f"  FETCHED {act_id}  ({len(data):,} bytes)", file=sys.stderr)
        except urllib.error.HTTPError as exc:
            if exc.code in {404, 410}:
                archive.store(missing_locator, b"404", storage_class="text")
                cached += 1
                events.append(
                    _prefetch_event(
                        statute_id=sid,
                        affecting_act_id=act_id,
                        url=url,
                        status="permanent_missing_cached",
                        rule_id="uk_prefetch_affecting_act_permanent_missing",
                        reason=f"http_{exc.code}",
                        blocking=False,
                    )
                )
                if verbose:
                    print(f"  HTTP {exc.code}: {act_id} (permanent miss)", file=sys.stderr)
                time.sleep(effective_delay)
                continue
            print(f"  HTTP {exc.code}: {url}", file=sys.stderr)
            errors += 1
            events.append(
                _prefetch_event(
                    statute_id=sid,
                    affecting_act_id=act_id,
                    url=url,
                    status="error",
                    rule_id="uk_prefetch_http_error",
                    reason=f"http_{exc.code}",
                    blocking=True,
                )
            )
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            print(f"  NETWORK ERROR: {url}: {exc}", file=sys.stderr)
            errors += 1
            events.append(
                _prefetch_event(
                    statute_id=sid,
                    affecting_act_id=act_id,
                    url=url,
                    status="error",
                    rule_id="uk_prefetch_network_error",
                    reason=exc.__class__.__name__,
                    blocking=True,
                )
            )

        time.sleep(effective_delay)

    return UKPrefetchReport(fetched, cached, errors, tuple(events))
