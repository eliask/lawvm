"""New Zealand Legislation API v0 acquisition into farchive.

This module is intentionally acquisition-only. It preserves API JSON, XML
manifestations, response headers, rate-limit state, and diagnostics without
claiming that any acquired surface is replay truth.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

_API_BASE = "https://api.legislation.govt.nz"
_USER_AGENT = "LawVM/0.1 (+https://lawvm.org)"
_RATE_LIMIT_HEADERS = (
    "X-RateLimit-Limit",
    "X-RateLimit-Remaining",
    "X-RateLimit-Reset",
)


class ArchiveWriter(Protocol):
    def get(self, locator: str, *, at: object | None = None) -> bytes | None: ...

    def store(
        self,
        locator: str,
        data: bytes,
        *,
        observed_at: datetime | None = None,
        storage_class: str | None = None,
        series_key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str: ...

    def close(self) -> None: ...


class ArchiveStore(ArchiveWriter, Protocol):
    def locators(self, pattern: str = "%") -> list[str]: ...


@dataclass(frozen=True)
class NZHttpResponse:
    status_code: int
    body: bytes
    headers: Mapping[str, str]
    content_type: str = ""


class NZTransport(Protocol):
    def get(self, url: str, *, api_key: str, accept: str) -> NZHttpResponse: ...


class UrllibNZTransport:
    """Small URL opener boundary that keeps API-key handling out of URLs."""

    def get(self, url: str, *, api_key: str, accept: str) -> NZHttpResponse:
        request = Request(url)
        request.add_header("User-Agent", _USER_AGENT)
        request.add_header("Accept", accept)
        request.add_header("X-Api-Key", api_key)
        try:
            with urlopen(request, timeout=60) as response:
                headers = {key: value for key, value in response.headers.items()}
                return NZHttpResponse(
                    status_code=response.getcode(),
                    body=response.read(),
                    headers=headers,
                    content_type=response.headers.get("Content-Type", ""),
                )
        except HTTPError as exc:
            headers = {key: value for key, value in exc.headers.items()}
            return NZHttpResponse(
                status_code=exc.code,
                body=exc.read(),
                headers=headers,
                content_type=exc.headers.get("Content-Type", ""),
            )
        except URLError as exc:
            reason = str(exc.reason).encode("utf-8", "replace")
            return NZHttpResponse(
                status_code=0,
                body=reason,
                headers={},
                content_type="text/plain",
            )


@dataclass(frozen=True)
class NZAcquisitionDiagnostic:
    rule_id: str
    phase: str
    family: str
    reason: str
    locator: str
    url: str
    status_code: int | None = None
    blocking: bool = False
    strict_disposition: str = "record"
    quirks_disposition: str = "record"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "phase": self.phase,
            "family": self.family,
            "reason": self.reason,
            "locator": self.locator,
            "url": self.url,
            "status_code": self.status_code,
            "blocking": self.blocking,
            "strict_disposition": self.strict_disposition,
            "quirks_disposition": self.quirks_disposition,
            "metadata": dict(self.metadata),
        }


@dataclass
class NZSyncOptions:
    db_path: Path
    search_term: str = ""
    work_ids: tuple[str, ...] = ()
    version_ids: tuple[str, ...] = ()
    legislation_type: str = ""
    publisher: str = ""
    version_sort: str = "desc"
    per_page: int = 100
    max_pages: int | None = None
    max_works: int | None = None
    max_versions: int | None = None
    max_versions_per_work: int | None = None
    include_xml: bool = True
    skip_existing: bool = True
    delay: float = 0.5
    request_budget: int | None = None
    reserve_remaining: int = 100
    sleep_on_rate_limit: bool = False
    max_sleep_seconds: int | None = None
    rate_limit_retry_attempts: int = 3
    diagnostics_jsonl: Path | None = None
    verbose: bool = False


@dataclass
class NZSyncStats:
    requests: int = 0
    cached: int = 0
    stored_json: int = 0
    stored_xml: int = 0
    skipped: int = 0
    works_seen: int = 0
    versions_seen: int = 0
    diagnostics: list[NZAcquisitionDiagnostic] = field(default_factory=list)
    stopped_reason: str = ""

    def as_summary(self) -> dict[str, Any]:
        return {
            "requests": self.requests,
            "cached": self.cached,
            "stored_json": self.stored_json,
            "stored_xml": self.stored_xml,
            "skipped": self.skipped,
            "works_seen": self.works_seen,
            "versions_seen": self.versions_seen,
            "diagnostics": len(self.diagnostics),
            "stopped_reason": self.stopped_reason,
        }


class NZRateLimitGate:
    """Conservative client-side gate for API v0 quota/burst protection."""

    def __init__(
        self,
        *,
        delay: float,
        request_budget: int | None,
        reserve_remaining: int,
    ) -> None:
        self.delay = max(delay, 0.0)
        self.request_budget = request_budget
        self.reserve_remaining = max(reserve_remaining, 0)
        self.requests = 0
        self.remaining: int | None = None
        self.reset_utc: str = ""
        self._last_request = 0.0

    def can_request(self) -> tuple[bool, str]:
        if self.request_budget is not None and self.requests >= self.request_budget:
            return False, "request_budget_exhausted"
        if self.remaining is not None and self.remaining <= self.reserve_remaining:
            return False, "rate_limit_reserve_reached"
        return True, ""

    def before_request(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if self._last_request and elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_request = time.monotonic()
        self.requests += 1

    def observe(self, headers: Mapping[str, str]) -> dict[str, Any]:
        limit_headers: dict[str, Any] = {}
        for key in _RATE_LIMIT_HEADERS:
            value = _header_get(headers, key)
            if value:
                limit_headers[key] = value
        remaining = _header_get(headers, "X-RateLimit-Remaining")
        if remaining:
            try:
                self.remaining = int(remaining)
            except ValueError:
                self.remaining = None
        reset = _header_get(headers, "X-RateLimit-Reset")
        if reset:
            self.reset_utc = reset
        return limit_headers

    def seconds_until_reset(self, *, buffer_seconds: int = 90) -> int:
        if not self.reset_utc:
            return 0
        raw = self.reset_utc.strip()
        reset_epoch: float | None = None
        try:
            reset_epoch = float(raw)
        except ValueError:
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                reset_epoch = None
            else:
                reset_epoch = parsed.timestamp()
        if reset_epoch is None:
            return 0
        return max(int(reset_epoch - time.time()) + buffer_seconds, 0)


class NZApiClient:
    def __init__(
        self,
        *,
        api_key: str,
        transport: NZTransport | None = None,
        rate_gate: NZRateLimitGate,
    ) -> None:
        if not api_key:
            raise ValueError("NZ_API_KEY is required")
        self._api_key = api_key
        self._transport = transport or UrllibNZTransport()
        self._rate_gate = rate_gate

    def get_json(self, url: str) -> tuple[NZHttpResponse | None, dict[str, Any], str]:
        ok, reason = self._rate_gate.can_request()
        if not ok:
            return None, {}, reason
        self._rate_gate.before_request()
        response = self._transport.get(url, api_key=self._api_key, accept="application/json")
        rate_headers = self._rate_gate.observe(response.headers)
        return response, rate_headers, ""

    def get_bytes(self, url: str) -> tuple[NZHttpResponse | None, dict[str, Any], str]:
        ok, reason = self._rate_gate.can_request()
        if not ok:
            return None, {}, reason
        self._rate_gate.before_request()
        response = self._transport.get(url, api_key=self._api_key, accept="application/xml, text/xml, */*")
        rate_headers = self._rate_gate.observe(response.headers)
        return response, rate_headers, ""

    def seconds_until_reset(self) -> int:
        return self._rate_gate.seconds_until_reset()


def nz_api_key_from_env() -> str:
    """Return the NZ API key without logging or exposing it."""
    return os.environ.get("NZ_API_KEY", "")


def sync_nz_corpus(
    archive: ArchiveWriter,
    *,
    api_key: str,
    options: NZSyncOptions,
    transport: NZTransport | None = None,
) -> NZSyncStats:
    """Sync NZ API v0 discovery data and XML manifestations into farchive."""
    stats = NZSyncStats()
    gate = NZRateLimitGate(
        delay=options.delay,
        request_budget=options.request_budget,
        reserve_remaining=options.reserve_remaining,
    )
    client = NZApiClient(api_key=api_key, transport=transport, rate_gate=gate)
    seen_work_ids: set[str] = set()
    version_ids: list[str] = list(dict.fromkeys(options.version_ids))

    for work_id in options.work_ids:
        seen_work_ids.add(work_id)

    if not options.work_ids and not options.version_ids:
        for work in _iter_search_works(archive, client, options, stats):
            work_id = _string_field(work, "work_id")
            if not work_id or work_id in seen_work_ids:
                continue
            if options.max_works is not None and len(seen_work_ids) >= options.max_works:
                break
            seen_work_ids.add(work_id)

    for work_id in sorted(seen_work_ids):
        work_versions_seen = 0
        for version in _fetch_work_versions(archive, client, work_id, options, stats):
            version_id = _string_field(version, "version_id")
            if version_id and version_id not in version_ids:
                version_ids.append(version_id)
                work_versions_seen += 1
                if options.max_versions is not None and len(version_ids) >= options.max_versions:
                    break
                if (
                    options.max_versions_per_work is not None
                    and work_versions_seen >= options.max_versions_per_work
                ):
                    break
        if options.max_versions is not None and len(version_ids) >= options.max_versions:
            break

    for version_id in version_ids:
        detail = _fetch_version_detail(archive, client, version_id, options, stats)
        if detail is None:
            continue
        stats.versions_seen += 1
        if options.include_xml:
            _fetch_xml_formats(archive, client, detail, options, stats)

    stats.requests = gate.requests
    _write_diagnostics(options.diagnostics_jsonl, stats.diagnostics)
    return stats


def _iter_search_works(
    archive: ArchiveWriter,
    client: NZApiClient,
    options: NZSyncOptions,
    stats: NZSyncStats,
) -> list[Mapping[str, Any]]:
    results: list[Mapping[str, Any]] = []
    page = 1
    while True:
        if options.max_pages is not None and page > options.max_pages:
            break
        params: dict[str, Any] = {
            "page": page,
            "per_page": min(max(options.per_page, 1), 100),
        }
        if options.search_term:
            params["search_term"] = options.search_term
        if options.legislation_type:
            params["legislation_type"] = options.legislation_type
        if options.publisher:
            params["publisher"] = options.publisher
        url = _api_url("/v0/works/", params)
        locator = url
        payload = _get_or_fetch_json(archive, client, locator, url, "nz_api_v0_works_page", options, stats)
        if payload is None:
            break
        page_results = _list_field(payload, "results")
        for row in page_results:
            if isinstance(row, Mapping):
                results.append(row)
        stats.works_seen += len(page_results)
        total = _int_field(payload, "total")
        per_page = _int_field(payload, "per_page") or min(max(options.per_page, 1), 100)
        if not page_results or total is None or page * per_page >= total:
            break
        page += 1
    return results


def _fetch_work_versions(
    archive: ArchiveWriter,
    client: NZApiClient,
    work_id: str,
    options: NZSyncOptions,
    stats: NZSyncStats,
) -> list[Mapping[str, Any]]:
    sort = "asc" if options.version_sort == "asc" else "desc"
    versions: list[Mapping[str, Any]] = []
    page = 1
    while True:
        params = {
            "sort": sort,
            "page": page,
            "per_page": min(max(options.per_page, 1), 100),
        }
        url = _api_url(f"/v0/works/{work_id}/versions/", params)
        payload = _get_or_fetch_json(
            archive,
            client,
            url,
            url,
            "nz_api_v0_work_versions",
            options,
            stats,
            series_key=f"nzleg://work/{work_id}/versions",
        )
        if payload is None:
            break
        page_results = _list_field(payload, "results")
        versions.extend(row for row in page_results if isinstance(row, Mapping))
        total = _int_field(payload, "total")
        per_page = _int_field(payload, "per_page") or min(max(options.per_page, 1), 100)
        if not page_results or total is None or page * per_page >= total:
            break
        page += 1
    return versions


def _fetch_version_detail(
    archive: ArchiveWriter,
    client: NZApiClient,
    version_id: str,
    options: NZSyncOptions,
    stats: NZSyncStats,
) -> Mapping[str, Any] | None:
    url = _api_url(f"/v0/versions/{version_id}/", {})
    return _get_or_fetch_json(
        archive,
        client,
        url,
        url,
        "nz_api_v0_version_detail",
        options,
        stats,
        series_key=f"nzleg://version/{version_id}/detail",
    )


def _fetch_xml_formats(
    archive: ArchiveWriter,
    client: NZApiClient,
    version_detail: Mapping[str, Any],
    options: NZSyncOptions,
    stats: NZSyncStats,
) -> None:
    version_id = _string_field(version_detail, "version_id")
    formats = _list_field(version_detail, "formats")
    xml_urls = [_format_url(row) for row in formats if _is_xml_format(row)]
    if not xml_urls:
        stats.diagnostics.append(
            NZAcquisitionDiagnostic(
                rule_id="nz_acquire_xml_format_missing",
                phase="acquisition",
                family="source_pathology",
                reason="version detail did not expose an XML format URL",
                locator=f"nzleg://version/{version_id}/format/xml",
                url="",
                blocking=False,
                metadata={"version_id": version_id},
            )
        )
        return
    for url in xml_urls:
        if not url:
            continue
        canonical_url = _canonicalize_version_format_url(url, version_id)
        _get_or_fetch_bytes(
            archive,
            client,
            canonical_url,
            canonical_url,
            "xml",
            "nz_api_v0_version_xml",
            options,
            stats,
            series_key=f"nzleg://version/{version_id}/format/xml",
            extra_metadata={
                "version_id": version_id,
                "api_format_url": url,
            },
        )


def _get_or_fetch_json(
    archive: ArchiveWriter,
    client: NZApiClient,
    locator: str,
    url: str,
    rule_id: str,
    options: NZSyncOptions,
    stats: NZSyncStats,
    *,
    series_key: str | None = None,
) -> Mapping[str, Any] | None:
    cached = archive.get(locator) if options.skip_existing else None
    if cached is not None:
        stats.cached += 1
        return _decode_json(cached, locator, url, stats)
    response, rate_headers, stopped_reason = _request_with_rate_limit_recovery(
        client,
        options,
        stats,
        locator=locator,
        url=url,
        accept_kind="json",
        rule_id=rule_id,
    )
    if response is None:
        if stopped_reason:
            _record_rate_limit_stop(stats, locator, url, stopped_reason, rate_headers)
        return None
    metadata = _response_metadata(rule_id, url, response, rate_headers)
    if response.status_code != 200:
        stats.diagnostics.append(_http_diagnostic(rule_id, locator, url, response, metadata))
        return None
    archive.store(
        locator,
        response.body,
        observed_at=datetime.now(UTC),
        storage_class="json",
        series_key=series_key,
        metadata=metadata,
    )
    stats.stored_json += 1
    return _decode_json(response.body, locator, url, stats)


def _get_or_fetch_bytes(
    archive: ArchiveWriter,
    client: NZApiClient,
    locator: str,
    url: str,
    storage_class: str,
    rule_id: str,
    options: NZSyncOptions,
    stats: NZSyncStats,
    *,
    series_key: str | None = None,
    extra_metadata: Mapping[str, Any] | None = None,
) -> bytes | None:
    cached = archive.get(locator) if options.skip_existing else None
    if cached is not None:
        stats.cached += 1
        return cached
    response, rate_headers, stopped_reason = _request_with_rate_limit_recovery(
        client,
        options,
        stats,
        locator=locator,
        url=url,
        accept_kind="bytes",
        rule_id=rule_id,
    )
    if response is None:
        if stopped_reason:
            _record_rate_limit_stop(stats, locator, url, stopped_reason, rate_headers)
        return None
    metadata = _response_metadata(rule_id, url, response, rate_headers)
    if extra_metadata:
        metadata.update(extra_metadata)
    if response.status_code != 200:
        stats.diagnostics.append(_http_diagnostic(rule_id, locator, url, response, metadata))
        return None
    archive.store(
        locator,
        response.body,
        observed_at=datetime.now(UTC),
        storage_class=storage_class,
        series_key=series_key,
        metadata=metadata,
    )
    if storage_class == "xml":
        stats.stored_xml += 1
    return response.body


def _request_with_rate_limit_recovery(
    client: NZApiClient,
    options: NZSyncOptions,
    stats: NZSyncStats,
    *,
    locator: str,
    url: str,
    accept_kind: str,
    rule_id: str,
) -> tuple[NZHttpResponse | None, dict[str, Any], str]:
    attempts = 0
    while True:
        if accept_kind == "json":
            response, rate_headers, stopped_reason = client.get_json(url)
        else:
            response, rate_headers, stopped_reason = client.get_bytes(url)

        if stopped_reason:
            if _should_sleep_for_stop(stopped_reason) and _sleep_for_rate_limit(
                client,
                options,
                reason=stopped_reason,
                url=url,
                status_code=None,
            ):
                continue
            return None, rate_headers, stopped_reason

        if response is None:
            return None, rate_headers, ""

        if response.status_code in {403, 429}:
            attempts += 1
            if attempts <= max(options.rate_limit_retry_attempts, 0):
                retry_sleep = _retry_after_seconds(response.headers) or min(2 ** attempts, 30)
                if options.verbose:
                    print(
                        f"NZ API {response.status_code}; retrying {rule_id} after {retry_sleep}s",
                        file=sys.stderr,
                    )
                time.sleep(retry_sleep)
                continue
            if _sleep_for_rate_limit(
                client,
                options,
                reason=f"http_{response.status_code}",
                url=url,
                status_code=response.status_code,
                headers=response.headers,
            ):
                attempts = 0
                continue

        return response, rate_headers, ""


def _record_rate_limit_stop(
    stats: NZSyncStats,
    locator: str,
    url: str,
    stopped_reason: str,
    rate_headers: Mapping[str, Any],
) -> None:
    stats.stopped_reason = stopped_reason
    stats.diagnostics.append(
        NZAcquisitionDiagnostic(
            rule_id="nz_acquire_rate_limit_stop",
            phase="acquisition",
            family="temporal_recovery",
            reason=stopped_reason,
            locator=locator,
            url=url,
            blocking=True,
            strict_disposition="block",
            metadata=rate_headers,
        )
    )


def _should_sleep_for_stop(reason: str) -> bool:
    return reason == "rate_limit_reserve_reached"


def _sleep_for_rate_limit(
    client: NZApiClient,
    options: NZSyncOptions,
    *,
    reason: str,
    url: str,
    status_code: int | None,
    headers: Mapping[str, str] | None = None,
) -> bool:
    if not options.sleep_on_rate_limit:
        return False
    sleep_seconds = _retry_after_seconds(headers or {}) or client.seconds_until_reset()
    if sleep_seconds <= 0:
        sleep_seconds = 60
    if options.max_sleep_seconds is not None and sleep_seconds > options.max_sleep_seconds:
        return False
    if options.verbose:
        code = f" status={status_code}" if status_code is not None else ""
        print(
            f"NZ API rate-limit wait: reason={reason}{code} sleep_seconds={sleep_seconds} url={url}",
            file=sys.stderr,
        )
    time.sleep(sleep_seconds)
    return True


def _retry_after_seconds(headers: Mapping[str, str]) -> int:
    raw = _header_get(headers, "Retry-After")
    if not raw:
        return 0
    try:
        return max(int(float(raw)), 0)
    except ValueError:
        return 0


def _decode_json(
    data: bytes,
    locator: str,
    url: str,
    stats: NZSyncStats,
) -> Mapping[str, Any] | None:
    try:
        value = json.loads(data.decode("utf-8"))
    except json.JSONDecodeError as exc:
        stats.diagnostics.append(
            NZAcquisitionDiagnostic(
                rule_id="nz_acquire_json_decode_failed",
                phase="acquisition",
                family="source_pathology",
                reason=str(exc),
                locator=locator,
                url=url,
                blocking=True,
                strict_disposition="block",
            )
        )
        return None
    if not isinstance(value, Mapping):
        stats.diagnostics.append(
            NZAcquisitionDiagnostic(
                rule_id="nz_acquire_json_shape_unexpected",
                phase="acquisition",
                family="source_pathology",
                reason="API JSON root was not an object",
                locator=locator,
                url=url,
                blocking=True,
                strict_disposition="block",
            )
        )
        return None
    return value


def _http_diagnostic(
    rule_id: str,
    locator: str,
    url: str,
    response: NZHttpResponse,
    metadata: Mapping[str, Any],
) -> NZAcquisitionDiagnostic:
    blocking = response.status_code in {401, 403, 429} or response.status_code == 0
    return NZAcquisitionDiagnostic(
        rule_id=f"{rule_id}_http_error",
        phase="acquisition",
        family="source_pathology",
        reason=f"HTTP status {response.status_code}",
        locator=locator,
        url=url,
        status_code=response.status_code,
        blocking=blocking,
        strict_disposition="block" if blocking else "record",
        metadata=metadata,
    )


def _response_metadata(
    rule_id: str,
    url: str,
    response: NZHttpResponse,
    rate_headers: Mapping[str, Any],
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "rule_id": rule_id,
        "phase": "acquisition",
        "source_regime": "nz_legislation_api_v0",
        "request_url_without_api_key": url,
        "status_code": response.status_code,
        "content_type": response.content_type,
        "retrieved_at": datetime.now(UTC).isoformat(),
    }
    if rate_headers:
        metadata["rate_limit"] = dict(rate_headers)
    return metadata


def _write_diagnostics(path: Path | None, diagnostics: list[NZAcquisitionDiagnostic]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for diagnostic in diagnostics:
            handle.write(json.dumps(diagnostic.to_jsonable(), ensure_ascii=False) + "\n")


def _api_url(path: str, params: Mapping[str, Any]) -> str:
    filtered = {key: value for key, value in params.items() if value not in ("", None)}
    query = urlencode(filtered)
    if query:
        return f"{_API_BASE}{path}?{query}"
    return f"{_API_BASE}{path}"


def _header_get(headers: Mapping[str, str], key: str) -> str:
    lowered = key.lower()
    for raw_key, value in headers.items():
        if raw_key.lower() == lowered:
            return value
    return ""


def _string_field(row: Mapping[str, Any], key: str) -> str:
    value = row.get(key)
    return value if isinstance(value, str) else ""


def _int_field(row: Mapping[str, Any], key: str) -> int | None:
    value = row.get(key)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _list_field(row: Mapping[str, Any], key: str) -> list[Any]:
    value = row.get(key)
    return value if isinstance(value, list) else []


def _format_url(row: object) -> str:
    if not isinstance(row, Mapping):
        return ""
    row_map = cast(Mapping[str, Any], row)
    value = row_map.get("url")
    return value if isinstance(value, str) else ""


def _is_xml_format(row: object) -> bool:
    if not isinstance(row, Mapping):
        return False
    row_map = cast(Mapping[str, Any], row)
    url = _format_url(row).lower()
    format_value = str(row_map.get("format") or row_map.get("type") or row_map.get("name") or "").lower()
    return url.endswith(".xml") or format_value == "xml" or "xml" in format_value


def _canonicalize_version_format_url(url: str, version_id: str) -> str:
    """Replace website ``latest`` aliases with concrete version URL segments."""
    version_date = _version_date_from_version_id(version_id)
    if not version_date:
        return url
    return url.replace("/latest.xml", f"/{version_date}.xml")


def _version_date_from_version_id(version_id: str) -> str:
    parts = version_id.rsplit("_", 1)
    if len(parts) != 2:
        return ""
    candidate = parts[1]
    return candidate if candidate else ""


def open_farchive(path: Path) -> ArchiveStore:
    from farchive import Farchive

    path.parent.mkdir(parents=True, exist_ok=True)
    return cast(ArchiveStore, Farchive(path))


def main(args: Any) -> None:
    api_key = nz_api_key_from_env()
    if not api_key:
        raise SystemExit("ERROR: NZ_API_KEY is not set")
    options = NZSyncOptions(
        db_path=Path(args.db),
        search_term=args.search_term,
        work_ids=tuple(args.work_id or ()),
        version_ids=tuple(args.version_id or ()),
        legislation_type=args.legislation_type or "",
        publisher=args.publisher or "",
        version_sort=args.version_sort,
        per_page=args.per_page,
        max_pages=args.max_pages,
        max_works=args.max_works,
        max_versions=args.max_versions,
        max_versions_per_work=args.max_versions_per_work,
        include_xml=not args.no_xml,
        skip_existing=not args.refetch,
        delay=args.delay,
        request_budget=args.request_budget,
        reserve_remaining=args.reserve_remaining,
        sleep_on_rate_limit=args.sleep_on_rate_limit,
        max_sleep_seconds=args.max_sleep_seconds,
        rate_limit_retry_attempts=args.rate_limit_retry_attempts,
        diagnostics_jsonl=Path(args.diagnostics_jsonl) if args.diagnostics_jsonl else None,
        verbose=args.verbose,
    )
    archive = open_farchive(options.db_path)
    try:
        stats = sync_nz_corpus(archive, api_key=api_key, options=options)
    finally:
        archive.close()
    summary = stats.as_summary()
    print(
        " ".join(
            f"{key}={value}"
            for key, value in summary.items()
            if key != "stopped_reason" or value
        )
    )
    if options.diagnostics_jsonl:
        print(f"diagnostics_jsonl={options.diagnostics_jsonl}")
    if stats.stopped_reason or any(d.blocking for d in stats.diagnostics):
        raise SystemExit(1)
