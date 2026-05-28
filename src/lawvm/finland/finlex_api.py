"""Finlex Open Data API v1 client.

Base: https://opendata.finlex.fi/finlex/avoindata/v1
Spec: Finlex_avoin_data_v0_4_0.yaml

Two actDocumentTypes matter:
  - statute           = enacted/original
  - statute-consolidated = consolidated (oracle)

URI scheme for consolidated statutes:
  .../akn/fi/act/statute-consolidated/{year}/{num}/{lang}@{pit_version}

Where pit_version is an 8-digit amendment-id label like "20110024"
(YYYYNNNN — year of the amendment followed by a 4-digit sequence number).

WARNING: the "current" endpoint (lang@, no pit suffix) returns the INITIAL
consolidation, not the latest. Always prefer lang@YYYYNNNN when available.

Pagination: the /list endpoint returns a JSON array. Keep incrementing ?page=N
until the response is an empty array [].

Rate limiting: no documented limit; we use 1 req/sec (conservative).
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
import time
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from lxml import etree
from lawvm.core.diagnostic_records import diagnostic_detail
from lawvm.finland.consolidated_artifacts import (
    build_canonical_consolidated_locator,
    canonical_consolidated_locator,
    extract_consolidated_xml_identity,
)
from lawvm.finland.helpers import _parse_iso_date

BASE_URL = "https://opendata.finlex.fi/finlex/avoindata/v1"
_USER_AGENT = "LawVM/0.1 (+https://github.com/lawvm)"

_CONSOLIDATED_COLLECTION_PAGE_LIMIT = 4
_CONSOLIDATED_COLLECTION_MAX_PAGES = 200

# Pattern to extract year/num/lang/pit from an akn_uri returned by the list endpoint.
# Example URI:
#   https://opendata.finlex.fi/finlex/avoindata/v1/akn/fi/act/statute-consolidated/2007/231/fin@20110024
_AKN_URI_RE = re.compile(
    r"/akn/fi/act/([^/]+)/(\d{4})/(\d+)/([a-z]{3})@([^/?#]*)"
)

_HTTP_MIN_DELAY_SECS = 1.0
_HTTP_LOCK = threading.Lock()
_LAST_HTTP_GET_AT = 0.0


@dataclass(frozen=True)
class ConsolidatedStoreWrite:
    """Write result for one canonical consolidated XML payload."""

    sid: str
    requested_locator: str | None
    canonical_locator: str
    embedded_version: str
    stored_locators: tuple[str, ...]


@dataclass(frozen=True)
class ConsolidatedPitCandidate:
    """One PIT candidate exposed by the consolidated collection endpoint."""

    version_tag: str
    date_consolidated: dt.date | None


def _http_get(url: str, accept: str = "application/json") -> bytes:
    """Minimal synchronous HTTP GET. Raises urllib.error.HTTPError on non-2xx."""
    global _LAST_HTTP_GET_AT
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": accept,
        },
    )
    max_retries = 7
    backoff_secs = 1.0
    last_exc: urllib.error.HTTPError | None = None
    for attempt in range(max_retries):
        with _HTTP_LOCK:
            now = time.monotonic()
            wait = _HTTP_MIN_DELAY_SECS - (now - _LAST_HTTP_GET_AT)
            if wait > 0:
                time.sleep(wait)
            _LAST_HTTP_GET_AT = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code == 429 and attempt + 1 < max_retries:
                time.sleep(backoff_secs)
                backoff_secs = min(backoff_secs * 2, 120.0)  # exponential, cap 2min
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise RuntimeError(f"HTTP GET failed for {url}: {exc}") from exc
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"HTTP GET failed without response for {url}")


def _extract_fin_pit_candidates_from_collection(
    raw: bytes,
) -> list[ConsolidatedPitCandidate]:
    """Extract Finnish PIT candidates from one consolidated-collection page."""
    try:
        root = etree.fromstring(raw)
    except etree.XMLSyntaxError:
        text = raw.decode("utf-8", errors="replace")
        return [
            ConsolidatedPitCandidate(version_tag=version, date_consolidated=None)
            for version in sorted(
                {
                    version
                    for version in re.findall(
                        r'/akn/fi/act/statute-consolidated/\d+/\d+/fin@(\d{8})/',
                        text,
                    )
                },
                key=lambda v: int(v),
            )
        ]

    candidates: list[ConsolidatedPitCandidate] = []
    for act in root.findall(".//{*}act"):
        version_tag = ""
        date_consolidated: dt.date | None = None

        for expr in act.findall(".//{*}FRBRExpression"):
            lang_el = expr.find(".//{*}FRBRlanguage")
            version_el = expr.find(".//{*}FRBRversionNumber")
            if lang_el is None or version_el is None:
                continue
            if lang_el.get("language") != "fin":
                continue
            version = (version_el.get("value") or "").strip()
            if version.isdigit() and len(version) == 8:
                version_tag = version
                break

        if not version_tag:
            continue

        for date_el in act.findall(".//{*}FRBRdate"):
            if date_el.get("name") != "dateConsolidated":
                continue
            date_consolidated = _parse_iso_date(date_el.get("date"))
            break

        candidates.append(
            ConsolidatedPitCandidate(
                version_tag=version_tag,
                date_consolidated=date_consolidated,
            )
        )

    if candidates:
        return candidates

    # Conservative fallback for odd collection payloads with no act-level structure.
    text = raw.decode("utf-8", errors="replace")
    return [
        ConsolidatedPitCandidate(version_tag=version, date_consolidated=None)
        for version in sorted(
            {
                version
                for version in re.findall(
                    r'/akn/fi/act/statute-consolidated/\d+/\d+/fin@(\d{8})/',
                    text,
                )
            },
            key=lambda v: int(v),
        )
    ]


def _collect_fin_pit_candidates_from_collection(
    year: str,
    num: str,
    *,
    max_pages: int = _CONSOLIDATED_COLLECTION_MAX_PAGES,
) -> list[ConsolidatedPitCandidate]:
    """Collect unique Finnish PIT candidates across the consolidated collection."""
    candidates: dict[str, ConsolidatedPitCandidate] = {}
    page_size = _CONSOLIDATED_COLLECTION_PAGE_LIMIT
    base = f"{BASE_URL}/akn/fi/act/statute-consolidated/{year}/{num}"

    for page in range(1, max_pages + 1):
        params = {"page": str(page), "limit": str(page_size)}
        url = base + "?" + urllib.parse.urlencode(params)
        try:
            raw = _http_get(url, accept="application/xml")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                break
            raise RuntimeError(
                f"Finlex API PIT listing failed: HTTP {exc.code} for {url}"
            ) from exc
        except RuntimeError as exc:
            raise RuntimeError(f"Finlex API PIT listing failed for {url}: {exc}") from exc

        page_candidates = _extract_fin_pit_candidates_from_collection(raw)
        if not page_candidates:
            break

        added = False
        for candidate in page_candidates:
            prev = candidates.get(candidate.version_tag)
            if prev is None or _candidate_sort_key(candidate) > _candidate_sort_key(prev):
                candidates[candidate.version_tag] = candidate
                added = True

        if not added:
            break

    return sorted(candidates.values(), key=_candidate_sort_key)


def _candidate_sort_key(candidate: ConsolidatedPitCandidate) -> tuple[int, int]:
    date_score = candidate.date_consolidated.toordinal() if candidate.date_consolidated else -1
    version_score = int(candidate.version_tag) if candidate.version_tag.isdigit() else -1
    return date_score, version_score

def _extract_fin_pit_version_from_xml(raw: bytes) -> str | None:
    """Extract the 8-digit Finnish PIT version from one consolidated XML blob."""
    version_tag = extract_consolidated_xml_identity(raw).embedded_version_tag
    return version_tag or None


# ---------------------------------------------------------------------------
# URI parsing
# ---------------------------------------------------------------------------

def parse_akn_uri(akn_uri: str) -> dict | None:
    """Parse an akn_uri from the list endpoint.

    Returns a dict with keys:
        doc_type    e.g. "statute-consolidated"
        year        e.g. "2007"
        num         e.g. "231"
        lang        e.g. "fin" or "swe"
        pit_version e.g. "20110024" (empty string if no PIT suffix;
                    this is the consolidated amendment-id label)
        akn_uri     the original URI (also the direct fetch URL)

    Returns None if the URI does not match the expected pattern.
    """
    m = _AKN_URI_RE.search(akn_uri)
    if not m:
        return None
    doc_type, year, num, lang, pit_version = m.groups()
    return {
        "doc_type": doc_type,
        "year": year,
        "num": num,
        "lang": lang,
        "pit_version": pit_version,
        "akn_uri": akn_uri,
    }


# ---------------------------------------------------------------------------
# list_changed_since
# ---------------------------------------------------------------------------

def list_changed_since(
    since: str,
    doc_type: str = "statute-consolidated",
    lang: str = "fin",
    limit: int = 1000,
) -> list[dict]:
    """List statutes changed since a datetime.

    Args:
        since:    ISO datetime, e.g. "2026-03-20T00:00:00Z"
        doc_type: "statute" or "statute-consolidated"
        lang:     filter to this language version (e.g. "fin"); None for both
        limit:    max results per page (API max is 10 per the spec; we iterate)

    Returns list of dicts with keys:
        akn_uri:     full fetch URL
        status:      "MODIFIED" | "ADDED" | "DELETED"
        year:        str (parsed from URI)
        num:         str (parsed from URI)
        lang:        str (e.g. "fin")
        pit_version: str (e.g. "20110024", may be empty)

    Handles pagination — follows page= until empty result.

    NOTE: The API's /list endpoint has a documented limit of 10 per page.
    We clamp to 10 to stay within spec and iterate pages automatically.
    """
    # API spec: Limit parameter maximum is 10 for the /list endpoint.
    page_size = min(limit, 10)

    params: dict[str, str] = {
        "publishedSince": since,
        "limit": str(page_size),
    }
    if lang:
        params["langAndVersion"] = f"{lang}@"

    base = f"{BASE_URL}/akn/fi/act/{doc_type}/list"
    results: list[dict] = []
    page = 1

    while True:
        params["page"] = str(page)
        url = base + "?" + urllib.parse.urlencode(params)
        try:
            raw = _http_get(url, accept="application/json")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"Finlex API list request failed: HTTP {exc.code} for {url}"
            ) from exc

        try:
            page_data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Finlex API returned non-JSON for {url}: {raw[:200]!r}"
            ) from exc

        if not page_data:
            break  # empty page = end of results

        for item in page_data:
            akn_uri = item.get("akn_uri", "")
            status = item.get("status", "UNKNOWN")
            parsed = parse_akn_uri(akn_uri)
            if parsed is None:
                # Unexpected URI format — include with raw fields only
                results.append({"akn_uri": akn_uri, "status": status,
                                 "year": "", "num": "", "lang": "", "pit_version": ""})
                continue
            results.append({
                "akn_uri": akn_uri,
                "status": status,
                "year": parsed["year"],
                "num": parsed["num"],
                "lang": parsed["lang"],
                "pit_version": parsed["pit_version"],
            })

        # If we got fewer results than page_size, we're on the last page.
        if len(page_data) < page_size:
            break

        page += 1

    return results


# ---------------------------------------------------------------------------
# PIT version discovery
# ---------------------------------------------------------------------------

def list_consolidated_pit_versions(
    year: str,
    num: str,
    *,
    max_pages: int = _CONSOLIDATED_COLLECTION_MAX_PAGES,
) -> list[str]:
    """List all Finnish PIT versions exposed by the consolidated collection API.

    The OpenAPI collection endpoint is paginated and may interleave language
    expressions.  We walk pages until the response stops yielding new Finnish
    ``fin@YYYYNNNN`` expressions and return the unique version numbers in
    ascending numeric order.
    """
    candidates = _collect_fin_pit_candidates_from_collection(
        year,
        num,
        max_pages=max_pages,
    )
    return sorted({candidate.version_tag for candidate in candidates}, key=lambda v: int(v))


# ---------------------------------------------------------------------------
# fetch_statute_xml
# ---------------------------------------------------------------------------

def fetch_statute_xml(
    year: str,
    num: str,
    doc_type: str = "statute-consolidated",
    lang_version: str = "fin@",
) -> bytes | None:
    """Fetch one statute XML from the API.

    URL: {BASE_URL}/akn/fi/act/{doc_type}/{year}/{num}/{lang_version}

    For consolidated with PIT: lang_version="fin@20210680"
    For consolidated "current" (WARNING: initial, not latest): lang_version="fin@"
    For enacted/original: doc_type="statute", lang_version="fin@"

    Returns raw AKN XML bytes, or None on 404.
    Raises RuntimeError on other HTTP errors.
    """
    url = f"{BASE_URL}/akn/fi/act/{doc_type}/{year}/{num}/{lang_version}"
    try:
        return _http_get(url, accept="application/xml")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise RuntimeError(
            f"Finlex API fetch failed: HTTP {exc.code} for {url}"
        ) from exc


# ---------------------------------------------------------------------------
# fetch_latest_consolidated
# ---------------------------------------------------------------------------

def fetch_latest_consolidated(
    year: str,
    num: str,
) -> tuple[bytes | None, str]:
    """Fetch the latest consolidated PIT for a statute.

    Strategy:
        Walk the paginated /akn/fi/act/statute-consolidated/{year}/{num}
        collection endpoint, rank Finnish fin@YYYYNNNN candidates by payload
        identity and dateConsolidated, then fetch the best available version.

    Returns (xml_bytes, pit_version) or (None, "").
    """
    pit_candidates = _collect_fin_pit_candidates_from_collection(year, num)
    if not pit_candidates:
        return None, ""

    for candidate in reversed(pit_candidates):
        xml = fetch_statute_xml(year, num, lang_version=f"fin@{candidate.version_tag}")
        if xml is not None:
            return xml, candidate.version_tag
    return None, ""


def fetch_latest_pit_xml(
    year: str,
    num: str,
) -> tuple[bytes | None, str]:
    """Compatibility alias for callers that want the latest consolidated PIT XML."""
    return fetch_latest_consolidated(year, num)


def _append_sync_latest_pit_diagnostic(
    diagnostics_out: list[dict[str, Any]] | None,
    *,
    rule_id: str,
    statute_id: str,
    reason: str,
    blocking: bool,
    pit_version: str = "",
    locator: str = "",
) -> None:
    if diagnostics_out is None:
        return
    diagnostics_out.append(
        diagnostic_detail(
            rule_id=rule_id,
            phase="acquisition",
            family="source_pathology",
            reason=reason,
            blocking=blocking,
            strict_disposition="block" if blocking else "record",
            quirks_disposition="record",
            statute_id=statute_id,
            pit_version=pit_version,
            locator=locator,
        )
    )


def sync_latest_pits(
    archive: Any,
    sids: list[str],
    *,
    delay: float = 1.0,
    verbose: bool = False,
    diagnostics_out: list[dict[str, Any]] | None = None,
) -> dict[str, int]:
    """Ensure all discovered PIT XMLs for each statute SID are cached in *archive*.

    For each statute:
      1. discover all Finnish PIT versions via the paginated OpenAPI collection
      2. skip fetching any exact finlex:// locator that already exists
      3. otherwise fetch and store each PIT version in ascending order

    Returns counts for cached, fetched, skipped, and errors.
    """
    from lawvm.corpus_store import oracle_url

    stats: dict[str, int] = {
        "cached": 0,
        "fetched": 0,
        "skipped": 0,
        "errors": 0,
        "statutes": 0,
    }

    for i, sid in enumerate(sids):
        year, num = sid.split("/", 1)
        stats["statutes"] += 1

        try:
            pit_versions = list_consolidated_pit_versions(year, num)
        except Exception as exc:
            stats["errors"] += 1
            _append_sync_latest_pit_diagnostic(
                diagnostics_out,
                rule_id="fi_sync_latest_pit_discovery_failed",
                statute_id=sid,
                reason=exc.__class__.__name__,
                blocking=True,
            )
            if verbose:
                print(f"[finlex_api] {sid}: PIT discovery failed: {exc}", file=sys.stderr)
            continue

        if not pit_versions:
            stats["skipped"] += 1
            _append_sync_latest_pit_diagnostic(
                diagnostics_out,
                rule_id="fi_sync_latest_pit_versions_missing",
                statute_id=sid,
                reason="no_pit_versions_found",
                blocking=False,
            )
            if verbose:
                print(f"[finlex_api] {sid}: no PIT versions found", file=sys.stderr)
            continue

        for j, pit_version in enumerate(pit_versions):
            target_locator = oracle_url(sid, version=pit_version)

            if archive.has(target_locator):
                stats["cached"] += 1
                if verbose:
                    print(f"[finlex_api] {sid}: cached {pit_version}", file=sys.stderr)
                continue

            if (i > 0 or j > 0) and delay > 0:
                time.sleep(delay)

            try:
                xml = fetch_statute_xml(year, num, lang_version=f"fin@{pit_version}")
            except Exception as exc:
                stats["errors"] += 1
                _append_sync_latest_pit_diagnostic(
                    diagnostics_out,
                    rule_id="fi_sync_latest_pit_fetch_failed",
                    statute_id=sid,
                    pit_version=pit_version,
                    locator=target_locator,
                    reason=exc.__class__.__name__,
                    blocking=True,
                )
                if verbose:
                    print(
                        f"[finlex_api] {sid}: fetch failed for {pit_version}: {exc}",
                        file=sys.stderr,
                    )
                continue

            if xml is None:
                stats["errors"] += 1
                _append_sync_latest_pit_diagnostic(
                    diagnostics_out,
                    rule_id="fi_sync_latest_pit_xml_missing",
                    statute_id=sid,
                    pit_version=pit_version,
                    locator=target_locator,
                    reason="fetch_returned_none",
                    blocking=True,
                )
                if verbose:
                    print(
                        f"[finlex_api] {sid}: no PIT XML available for {pit_version}",
                        file=sys.stderr,
                    )
                continue

            store_consolidated_xml(
                archive,
                sid,
                xml,
                requested_locator=target_locator,
                storage_class="xml",
            )
            stats["fetched"] += 1
            if verbose:
                print(
                    f"[finlex_api] {sid}: stored {pit_version} "
                    f"({len(xml):,} bytes)",
                    file=sys.stderr,
                )

    return stats


def store_consolidated_xml(
    archive: Any,
    sid: str,
    xml: bytes,
    *,
    requested_locator: str | None = None,
    storage_class: str = "xml",
) -> ConsolidatedStoreWrite:
    """Store one consolidated statute XML under its canonical versioned locator."""
    embedded_version = _extract_fin_pit_version_from_xml(xml)
    if not embedded_version:
        raise ValueError(f"consolidated XML missing embedded version identity for {sid}")
    source_locator = requested_locator or build_canonical_consolidated_locator(
        sid=sid,
        lang="fin",
        version_tag=embedded_version,
        rest="main.xml",
    )
    canonical_locator = canonical_consolidated_locator(
        source_locator,
        version_tag=embedded_version,
    )
    locators = [canonical_locator]

    if hasattr(archive, "store_batch"):
        batch = [(locator, xml) for locator in locators]
        if batch:
            archive.store_batch(batch, storage_class=storage_class)
    else:
        for locator in locators:
            archive.store(locator, xml, storage_class=storage_class)

    return ConsolidatedStoreWrite(
        sid=sid,
        requested_locator=requested_locator,
        canonical_locator=canonical_locator,
        embedded_version=embedded_version,
        stored_locators=tuple(locators),
    )


# ---------------------------------------------------------------------------
# archive URL helpers
# ---------------------------------------------------------------------------

def _archive_url(year: str, num: str, lang: str, pit_version: str) -> str:
    """Canonical finlex:// URL for storage in Farchive."""
    if not pit_version:
        raise ValueError(f"versioned consolidated locator required for {year}/{num}")
    return build_canonical_consolidated_locator(
        sid=f"{year}/{num}",
        lang=lang,
        version_tag=pit_version,
        rest="main.xml",
    )


# ---------------------------------------------------------------------------
# sync_changes
# ---------------------------------------------------------------------------

def sync_changes(
    archive: Any,
    since: str,
    delay: float = 1.0,
    lang: str = "fin",
    doc_type: str = "statute-consolidated",
    dry_run: bool = False,
    verbose: bool = False,
) -> dict:
    """Incremental sync: fetch all changes since datetime, store in archive.

    Args:
        archive:  Farchive to store fetched XMLs
        since:    ISO datetime for publishedSince parameter
        delay:    seconds between requests (default 1.0 — conservative)
        lang:     language filter (default "fin")
        doc_type: document type to sync (default "statute-consolidated")
        dry_run:  if True, list changes but do not fetch/store XMLs
        verbose:  if True, print one line per statute to stderr

    Returns:
        {"fetched": N, "modified": N, "added": N, "deleted": N,
         "skipped": N, "errors": N}
    """
    stats: dict[str, int] = {
        "fetched": 0, "modified": 0, "added": 0,
        "deleted": 0, "skipped": 0, "errors": 0,
    }

    print(
        f"[finlex_api] Listing {doc_type} changes since {since} (lang={lang})...",
        file=sys.stderr,
    )
    changes = list_changed_since(since, doc_type=doc_type, lang=lang)
    print(
        f"[finlex_api] {len(changes)} change(s) found.",
        file=sys.stderr,
    )

    if dry_run:
        for item in changes:
            print(
                f"  {item['status']:10s}  {item['year']}/{item['num']}  "
                f"pit={item['pit_version'] or '(no pit)'}  {item['akn_uri']}",
            )
        stats["fetched"] = 0
        for item in changes:
            s = item["status"].upper()
            if s == "MODIFIED":
                stats["modified"] += 1
            elif s in ("ADDED", "NEW"):
                stats["added"] += 1
            elif s == "DELETED":
                stats["deleted"] += 1
        return stats

    for i, item in enumerate(changes):
        status = item["status"].upper()
        year = item["year"]
        num = item["num"]
        lang_item = item["lang"]
        pit = item["pit_version"]
        akn_uri = item["akn_uri"]

        # Track status counts
        if status == "MODIFIED":
            stats["modified"] += 1
        elif status in ("ADDED", "NEW"):
            stats["added"] += 1
        elif status == "DELETED":
            stats["deleted"] += 1

        if status == "DELETED":
            # Nothing to fetch for deleted statutes — just track count.
            if verbose:
                print(
                    f"  [{i+1}/{len(changes)}] DELETED  {year}/{num}  (skip fetch)",
                    file=sys.stderr,
                )
            stats["skipped"] += 1
            continue

        if not year or not num:
            stats["errors"] += 1
            print(
                f"  [{i+1}/{len(changes)}] ERROR: could not parse URI: {akn_uri}",
                file=sys.stderr,
            )
            continue

        # Check archive cache — skip if already stored (immutable PIT).
        archive_url = _archive_url(year, num, lang_item, pit)
        if pit and archive.has(archive_url):
            if verbose:
                print(
                    f"  [{i+1}/{len(changes)}] CACHED   {year}/{num}  pit={pit}",
                    file=sys.stderr,
                )
            stats["skipped"] += 1
            continue

        # Rate-limit between fetches (not on first item).
        if i > 0 and delay > 0:
            time.sleep(delay)

        # Fetch directly from the akn_uri returned by the list endpoint.
        try:
            req = urllib.request.Request(
                akn_uri,
                headers={"User-Agent": _USER_AGENT, "Accept": "application/xml"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                xml_bytes = resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                if verbose:
                    print(
                        f"  [{i+1}/{len(changes)}] 404      {year}/{num}  pit={pit}",
                        file=sys.stderr,
                    )
                stats["errors"] += 1
                continue
            print(
                f"  [{i+1}/{len(changes)}] HTTP {exc.code}  {year}/{num}  {akn_uri}",
                file=sys.stderr,
            )
            stats["errors"] += 1
            continue
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            print(
                f"  [{i+1}/{len(changes)}] NET_ERR  {year}/{num}  {exc}",
                file=sys.stderr,
            )
            stats["errors"] += 1
            continue

        archive.store(archive_url, xml_bytes, storage_class="xml")
        stats["fetched"] += 1

        if verbose:
            print(
                f"  [{i+1}/{len(changes)}] {status:10s}  {year}/{num}  "
                f"pit={pit or '(no pit)'}  {len(xml_bytes):,}b",
                file=sys.stderr,
            )

    return stats
