"""uk_prefetch.py — Pre-fetch missing affecting act XMLs into the UK Farchive.

Shared library used by:
  - scripts/fetch_uk_affecting_acts.py  (batch / bench-corpus mode)
  - lawvm uk-fetch-affecting             (single-statute CLI)
  - lawvm uk-replay --fetch-missing      (inline pre-fetch before replay)
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from collections import Counter
from dataclasses import dataclass
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, NamedTuple

from lawvm.core.compile_records import is_blocking_compile_record
from lawvm.core.diagnostic_records import diagnostic_detail
from lawvm.core.http_identity import LAWVM_USER_AGENT
from lawvm.uk_legislation.source_state import UKSourceStatus, classify_uk_source_blob

_LEG_BASE = "https://www.legislation.gov.uk"
_USER_AGENT = LAWVM_USER_AGENT

# Minimum inter-request delay (seconds).
_MIN_DELAY = 0.5


def fetch_affecting_act(act_id: str, out_path: Path, dry_run: bool = False) -> bool:
    """Fetch one affecting act XML to the legacy filesystem cache."""
    url = f"{_LEG_BASE}/{act_id}/data.xml"
    print(f"  fetch {url} -> {out_path}")
    if dry_run:
        return True
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        out_path.write_bytes(data)
        meta = {
            "url": url,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        out_path.with_suffix(".xml.meta.json").write_text(
            json.dumps(meta, indent=2) + "\n",
            encoding="utf-8",
        )
        return True
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
        print(f"  ERROR fetching {url}: {exc}")
        return False


def fetch_affecting_acts_from_manifest(
    manifest: dict[str, Any], repo_root: Path, dry_run: bool = False, limit: int | None = None
) -> tuple[int, int]:
    """Fetch all filesystem artifacts described by a UK acquisition manifest."""
    sources = manifest.get("sources", [])
    if limit:
        sources = sources[:limit]
    ok = fail = 0
    for src in sources:
        for artifact in src.get("artifacts", []):
            out = repo_root / artifact["path"]
            if out.exists():
                ok += 1
                continue
            success = fetch_affecting_act(src["act_id"], out, dry_run=dry_run)
            if success:
                ok += 1
            else:
                fail += 1
    return ok, fail


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
        event_rule_counts = Counter(str(event.get("rule_id") or "unknown") for event in self.events)
        blocking_event_rule_counts = Counter(
            str(event.get("rule_id") or "unknown")
            for event in self.events
            if is_blocking_compile_record(event)
        )
        return {
            "fetched_count": self.fetched_count,
            "already_cached_count": self.already_cached_count,
            "error_count": self.error_count,
            "event_count": len(self.events),
            "event_rule_counts": dict(sorted(event_rule_counts.items())),
            "blocking_event_count": sum(blocking_event_rule_counts.values()),
            "blocking_event_rule_counts": dict(sorted(blocking_event_rule_counts.items())),
            "events": [dict(event) for event in self.events],
        }


class _EnactedSourcePrefetchResult(NamedTuple):
    fetched_count: int
    already_cached_count: int
    error_count: int
    events: tuple[dict[str, Any], ...]


def _missing_affecting_locator(act_id: str) -> str:
    """Negative-cache locator for permanently missing affecting act XML."""
    return f"leg://missing/uk/{act_id}/data.xml"


def _missing_affecting_enacted_locator(act_id: str) -> str:
    """Negative-cache locator for permanently missing enacted affecting act XML."""
    return f"leg://missing/uk/{act_id}/enacted/data.xml"


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
    return diagnostic_detail(
        rule_id=rule_id,
        family="source_pathology",
        phase="acquisition",
        reason=reason,
        blocking=blocking,
        statute_id=statute_id,
        affecting_act_id=affecting_act_id,
        locator=_missing_affecting_locator(affecting_act_id),
        url=url,
        status=status,
    )


def _prefetch_fetched_event(
    *,
    statute_id: str,
    affecting_act_id: str,
    url: str,
    data: bytes,
    final_url: str,
    http_status: int | None,
) -> dict[str, Any]:
    return diagnostic_detail(
        rule_id="uk_prefetch_affecting_act_fetched",
        family="source_witness",
        phase="acquisition",
        blocking=False,
        statute_id=statute_id,
        affecting_act_id=affecting_act_id,
        locator=url,
        url=url,
        final_url=final_url,
        http_status=http_status,
        status="fetched",
        bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _prefetch_cached_event(
    *,
    statute_id: str,
    affecting_act_id: str,
    url: str,
    data: bytes,
) -> dict[str, Any]:
    return diagnostic_detail(
        rule_id="uk_prefetch_affecting_act_cached",
        family="source_witness",
        phase="acquisition",
        blocking=False,
        statute_id=statute_id,
        affecting_act_id=affecting_act_id,
        locator=url,
        url=url,
        status="cached",
        bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _prefetch_would_fetch_event(
    *,
    statute_id: str,
    affecting_act_id: str,
    url: str,
) -> dict[str, Any]:
    return diagnostic_detail(
        rule_id="uk_prefetch_affecting_act_would_fetch",
        family="source_witness",
        phase="acquisition",
        blocking=False,
        statute_id=statute_id,
        affecting_act_id=affecting_act_id,
        locator=url,
        url=url,
        status="dry_run_would_fetch",
    )


def fetch_missing_for_statute(
    sid: str,
    archive: Any,  # Farchive — avoid circular import at module level
    delay: float = 0.8,
    dry_run: bool = False,
    verbose: bool = False,
    include_enacted: bool = False,
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
    from lawvm.uk_legislation.effects import (
        load_effects_for_statute_from_archive,
        get_affecting_act_enacted_xml_from_archive,
        get_affecting_act_xml_from_archive,
        uk_effect_requires_affecting_source_for_replay,
    )

    events: list[dict[str, Any]] = []
    effect_feed_parse_rejections: list[dict[str, Any]] = []
    effects = load_effects_for_statute_from_archive(
        sid,
        archive,
        parse_rejections_out=effect_feed_parse_rejections,
    )
    for rejection in effect_feed_parse_rejections:
        event = dict(rejection)
        event.setdefault("statute_id", sid)
        events.append(event)
    source_error_count = sum(1 for event in events if is_blocking_compile_record(event))
    source_required = [
        e
        for e in effects
        if uk_effect_requires_affecting_source_for_replay(e)
    ]

    if not source_required:
        if verbose:
            print(f"  {sid}: no source-required effects in archive", file=sys.stderr)
        return UKPrefetchReport(0, 0, source_error_count, tuple(events))

    effective_delay = max(delay, _MIN_DELAY)
    fetched = cached = 0
    errors = source_error_count
    seen_acts: set[str] = set()

    def _ensure_enacted_cached(act_id: str) -> _EnactedSourcePrefetchResult:
        if not include_enacted:
            return _EnactedSourcePrefetchResult(0, 0, 0, ())

        enacted_url = f"{_LEG_BASE}/{act_id}/enacted/data.xml"
        missing_enacted_locator = _missing_affecting_enacted_locator(act_id)
        if archive.has(missing_enacted_locator):
            return _EnactedSourcePrefetchResult(
                0,
                1,
                0,
                (
                    _prefetch_event(
                        statute_id=sid,
                        affecting_act_id=act_id,
                        url=enacted_url,
                        status="skipped_cached_permanent_missing",
                        rule_id="uk_prefetch_affecting_enacted_permanent_missing_marker_skipped",
                        reason="permanent_missing_marker_cached",
                        blocking=False,
                    ),
                ),
            )

        enacted_xml = get_affecting_act_enacted_xml_from_archive(act_id, archive)
        if enacted_xml:
            return _EnactedSourcePrefetchResult(
                0,
                1,
                0,
                (
                    _prefetch_cached_event(
                        statute_id=sid,
                        affecting_act_id=act_id,
                        url=enacted_url,
                        data=enacted_xml,
                    ),
                ),
            )

        if dry_run:
            print(f"  DRY-RUN would fetch enacted: {enacted_url}")
            return _EnactedSourcePrefetchResult(
                1,
                0,
                0,
                (
                    _prefetch_would_fetch_event(
                        statute_id=sid,
                        affecting_act_id=act_id,
                        url=enacted_url,
                    ),
                ),
            )

        req = urllib.request.Request(enacted_url, headers={"User-Agent": _USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                final_url = str(getattr(resp, "geturl", lambda: enacted_url)())
                http_status = getattr(resp, "status", None)
            source_state = classify_uk_source_blob(data)
            if source_state.status is not UKSourceStatus.AVAILABLE:
                return _EnactedSourcePrefetchResult(
                    0,
                    0,
                    1,
                    (
                        _prefetch_event(
                            statute_id=sid,
                            affecting_act_id=act_id,
                            url=enacted_url,
                            status="error",
                            rule_id="uk_prefetch_affecting_enacted_suspicious_small_response",
                            reason=f"suspiciously_small_response:{source_state.size}",
                            blocking=True,
                        ),
                    ),
                )
            archive.store(enacted_url, data, storage_class="xml")
            return _EnactedSourcePrefetchResult(
                1,
                0,
                0,
                (
                    _prefetch_fetched_event(
                        statute_id=sid,
                        affecting_act_id=act_id,
                        url=enacted_url,
                        data=data,
                        final_url=final_url,
                        http_status=http_status,
                    ),
                ),
            )
        except urllib.error.HTTPError as exc:
            if exc.code in {404, 410}:
                archive.store(missing_enacted_locator, b"404", storage_class="text")
                return _EnactedSourcePrefetchResult(
                    0,
                    1,
                    0,
                    (
                        _prefetch_event(
                            statute_id=sid,
                            affecting_act_id=act_id,
                            url=enacted_url,
                            status="permanent_missing_cached",
                            rule_id="uk_prefetch_affecting_enacted_permanent_missing",
                            reason=f"http_{exc.code}",
                            blocking=False,
                        ),
                    ),
                )
            return _EnactedSourcePrefetchResult(
                0,
                0,
                1,
                (
                    _prefetch_event(
                        statute_id=sid,
                        affecting_act_id=act_id,
                        url=enacted_url,
                        status="error",
                        rule_id="uk_prefetch_affecting_enacted_http_error",
                        reason=f"http_{exc.code}",
                        blocking=True,
                    ),
                ),
            )
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            return _EnactedSourcePrefetchResult(
                0,
                0,
                1,
                (
                    _prefetch_event(
                        statute_id=sid,
                        affecting_act_id=act_id,
                        url=enacted_url,
                        status="error",
                        rule_id="uk_prefetch_affecting_enacted_network_error",
                        reason=exc.__class__.__name__,
                        blocking=True,
                    ),
                ),
            )

    for e in source_required:
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

        url = f"{_LEG_BASE}/{act_id}/data.xml"

        # Check cache first — no network round-trip if already stored.
        xml = get_affecting_act_xml_from_archive(act_id, archive)
        if xml:
            cached += 1
            events.append(
                _prefetch_cached_event(
                    statute_id=sid,
                    affecting_act_id=act_id,
                    url=url,
                    data=xml,
                )
            )
            if verbose:
                print(f"  CACHED  {act_id}", file=sys.stderr)
            enacted_result = _ensure_enacted_cached(act_id)
            fetched += enacted_result.fetched_count
            cached += enacted_result.already_cached_count
            errors += enacted_result.error_count
            events.extend(enacted_result.events)
            continue

        if dry_run:
            print(f"  DRY-RUN would fetch: {url}")
            fetched += 1  # count "would-fetch" as fetched for summary purposes
            events.append(
                _prefetch_would_fetch_event(
                    statute_id=sid,
                    affecting_act_id=act_id,
                    url=url,
                )
            )
            continue

        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                final_url = str(getattr(resp, "geturl", lambda url=url: url)())
                http_status = getattr(resp, "status", None)
            source_state = classify_uk_source_blob(data)
            if source_state.status is not UKSourceStatus.AVAILABLE:
                print(
                    f"  WARN: suspiciously small response ({source_state.size} bytes): {url}",
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
                        reason=f"suspiciously_small_response:{source_state.size}",
                        blocking=True,
                    )
                )
            else:
                archive.store(url, data, storage_class="xml")
                events.append(
                    _prefetch_fetched_event(
                        statute_id=sid,
                        affecting_act_id=act_id,
                        url=url,
                        data=data,
                        final_url=final_url,
                        http_status=http_status,
                    )
                )
                fetched += 1
                if verbose:
                    print(f"  FETCHED {act_id}  ({len(data):,} bytes)", file=sys.stderr)
                enacted_result = _ensure_enacted_cached(act_id)
                fetched += enacted_result.fetched_count
                cached += enacted_result.already_cached_count
                errors += enacted_result.error_count
                events.extend(enacted_result.events)
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
