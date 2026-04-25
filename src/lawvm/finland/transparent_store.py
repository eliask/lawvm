"""transparent_store.py — Multi-source oracle CorpusStore for Finnish statutes.

Implements the refresh cascade described in:
  docs/TRANSPARENT_CORPUS_STORE_SPEC_2026-03-26.md

Source priority:
  1. Cached PIT-versioned XML from Finlex Open Data API (fin@YYYYNNNN)
  2. Legacy unversioned consolidated XML may still exist in old archives,
     but this backend no longer treats it as authoritative.

The HTML oracle (finlex_html.py) is used as a FRESHNESS CHECK only:
  - Compare section counts: HTML vs cached PIT XML
  - If HTML count > PIT count → PIT is stale, trigger refresh
  - HTML itself is never used as the content oracle (wrong format for replay)

Default read policy:
  - ordinary read_oracle/read_source/read_amendment calls are archive-only
  - live refresh is reserved for explicit refresh methods

Rate limiting:
  - API calls: 1 req/sec (finlex_api.py rate-limits by sleep between calls)
  - HTML calls: 1 req/sec (finlex_html.py module-level throttle)

Thread safety: Farchive uses POSIX fcntl locking for multi-process access.
All methods here are synchronous (no asyncio).

Usage:
    from lawvm.finland.transparent_store import TransparentCorpusStore
    from farchive import Farchive

    archive = Farchive("data/finlex.farchive")
    store = TransparentCorpusStore(archive)
    xml = store.read_oracle("2002/738")

Or via factory:
    LAWVM_CORPUS_STORE=transparent uv run lawvm diff 2002/738
"""

from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from lawvm.corpus_store import CorpusStore, oracle_url
from lawvm.finland.corpus import (
    list_cached_consolidated_locators,
    list_cached_consolidated_pit_locators,
    list_cached_corrigendum_locators,
)
from lawvm.finland.consolidated_artifacts import (
    build_consolidated_listing_locator,
    build_missing_consolidated_locator,
    ConsolidatedArtifactSelector,
    parse_versioned_consolidated_main_locator,
)
from lawvm.finland.consolidated_store import (
    best_cached_consolidated_path_index,
    select_cached_consolidated_artifact,
    select_cached_consolidated_path_index,
)
from lawvm.finland.finlex_api import (
    list_consolidated_pit_versions,
    fetch_latest_consolidated,
    store_consolidated_xml,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Farchive locator scheme for the transparent store.
# PIT listings use a Finland-owned cache key; PIT XML uses the canonical
# versioned consolidated locator family from corpus_store/oracle_url().

# API base for PIT discovery (NOT the v1 list endpoint — this is the v2 REST endpoint)
_API_BASE = "https://opendata.finlex.fi/finlex/avoindata/v1/akn/fi/act/statute-consolidated"

# Max age for the PIT version listing cache (it grows monotonically, so 7 days is safe)
_PIT_LISTING_MAX_AGE_DAYS: float = 7.0

# Max age for PIT XML itself (immutable by definition — could be infinite, but
# we use 30 days to allow for rare re-publications)
_PIT_XML_MAX_AGE_DAYS: float = 30.0

# How many seconds between API requests when batch-refreshing
_API_RATE_LIMIT_SECS: float = 1.0

# User-agent for the PIT listing request (not html, so urllib is fine)
_USER_AGENT = "LawVM/0.1 (+https://github.com/lawvm)"

# Pattern for PIT suffix in directory listing response.
# The API returns HTML-like directory listing with entries like:
#   <a href="fin@20210680/">fin@20210680/</a>
# or plain text lines like:
#   fin@20210680/
_PIT_DIR_ENTRY_RE = re.compile(r'\bfin@(\d{8})\b')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _age_days(iso_ts: str) -> float:
    dt = datetime.strptime(iso_ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0


def _parse_cached_pit_listing(raw: bytes) -> list[str]:
    """Parse a cached PIT listing payload.

    Older cache entries store the raw API directory listing; newer ones store
    a newline-separated list of PIT versions.  This parser accepts both.
    """
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return []
    versions = _PIT_DIR_ENTRY_RE.findall(text)
    if versions:
        return sorted(set(versions), key=lambda v: int(v))
    versions = [line.strip() for line in text.splitlines() if line.strip().isdigit()]
    return sorted(set(versions), key=lambda v: int(v))


def _http_get_bytes(url: str) -> bytes | None:
    """Synchronous HTTP GET. Returns None on 404; raises RuntimeError on other errors."""
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    backoffs = (0.0, 5.0, 15.0)
    for idx, delay in enumerate(backoffs):
        if delay > 0:
            time.sleep(delay)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            if exc.code == 429 and idx + 1 < len(backoffs):
                continue
            raise RuntimeError(
                f"TransparentCorpusStore: HTTP {exc.code} for {url}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise RuntimeError(
                f"TransparentCorpusStore: network error for {url}: {exc}"
            ) from exc
    return None


def _source_missing_url(sid: str) -> str:
    """Local negative-cache URL for missing source/amendment XML."""
    return f"finlex://missing/sd/{sid}/fin/main.xml"


def _oracle_missing_url(sid: str) -> str:
    """Local negative-cache URL for missing consolidated/oracle XML."""
    return build_missing_consolidated_locator(sid=sid)


def _load_known_missing_sources() -> frozenset[str]:
    """Load the repo-managed permanent-404 list from data/finland/known_missing_sources.txt.

    These are statute/amendment IDs confirmed to have no source XML in the Finlex
    Open Data API. The file is updated intentionally (never auto-rewritten at runtime)
    so new genuine 404s show up as detectable additions rather than silent farchive markers.
    """
    candidates = [
        Path(__file__).parent.parent.parent.parent / "data" / "finland" / "known_missing_sources.txt",
        Path("data/finland/known_missing_sources.txt"),
    ]
    for p in candidates:
        if p.exists():
            return frozenset(line.strip() for line in p.read_text().splitlines() if line.strip())
    return frozenset()


# Loaded once at import time — safe because the file is repo-managed and read-only at runtime.
_KNOWN_MISSING_SOURCES: frozenset[str] = _load_known_missing_sources()


def is_known_missing_source(sid: str) -> bool:
    """Return True if sid is a confirmed permanent 404 in the Finlex Open Data API.

    These IDs are declared in data/finland/known_missing_sources.txt and will
    never have source XML available. Callers can use this to skip replay rather
    than letting it fail with FileNotFoundError.
    """
    return sid in _KNOWN_MISSING_SOURCES


# ---------------------------------------------------------------------------
# FreshnessReport
# ---------------------------------------------------------------------------

@dataclass
class StatuteFreshness:
    """Freshness record for one statute."""
    sid: str
    source: str          # "api_pit" | "not_found"
    pit_version: str     # e.g. "20210680", or "" if none
    last_seen: str       # ISO timestamp of latest observation, or ""
    age_days: float      # days since last_seen, or -1.0
    section_count_xml: int | None   # from XML oracle
    section_count_html: int | None  # from HTML (freshness check)
    stale: bool          # True if HTML count > XML count or age > threshold


@dataclass
class FreshnessReport:
    """Aggregated freshness across all statutes."""
    total: int = 0
    fresh: int = 0
    stale: int = 0
    no_pit: int = 0
    zip_only: int = 0
    not_found: int = 0
    rows: list[StatuteFreshness] = field(default_factory=list)

    def print_summary(self, file=None) -> None:
        if file is None:
            file = sys.stderr
        print(f"Freshness report: {self.total} statutes", file=file)
        print(f"  FRESH:    {self.fresh}", file=file)
        print(f"  STALE:    {self.stale}", file=file)
        print(f"  NO_PIT:   {self.no_pit}", file=file)
        print(f"  MISSING:  {self.not_found}", file=file)


# ---------------------------------------------------------------------------
# TransparentCorpusStore
# ---------------------------------------------------------------------------

class TransparentCorpusStore(CorpusStore):
    """Farchive-backed oracle CorpusStore for Finnish statutes.

    read_oracle(sid) cascade:
      1. Return cached PIT XML if fresh (< pit_xml_max_age_days old).
      2. Discover available PIT versions from the API directory listing.
      3. Fetch the highest-numbered PIT XML and cache it.
      4. Stale cached PIT as last resort.

    read_source / read_amendment read from Farchive (finlex://sd/... locators).
    list_statute_ids derives from Farchive locators.
    oracle_path_index reports the best oracle URL for each SID.
    """

    def __init__(
        self,
        archive: Any,
        cache_only: bool = False,
        pit_listing_max_age_days: float = _PIT_LISTING_MAX_AGE_DAYS,
        pit_xml_max_age_days: float = _PIT_XML_MAX_AGE_DAYS,
        api_rate_limit_secs: float = _API_RATE_LIMIT_SECS,
        verbose: bool = False,
    ) -> None:
        """Create a TransparentCorpusStore.

        Args:
            archive: Farchive for caching PIT XMLs and source/amendment XMLs.
                     Source/amendment XMLs (finlex://sd/...) live in the same DB.
            cache_only: If True, never fetch live from Finlex; use cached
                          archive content only.
            pit_listing_max_age_days: How old a PIT listing can be before
                          re-fetching. Default 7 days.
            pit_xml_max_age_days: How old a cached PIT XML can be before
                          re-fetching. Default 30 days.
            api_rate_limit_secs: Minimum seconds between API requests.
            verbose: If True, print fetch activity to stderr.
        """
        self._archive = archive
        self._cache_only = cache_only
        # All source/amendment XMLs live in the same Farchive DB via finlex://sd/... locators.
        self._listing_max_age_h = pit_listing_max_age_days * 24.0
        self._pit_xml_max_age_h = pit_xml_max_age_days * 24.0
        self._api_delay = api_rate_limit_secs
        self._verbose = verbose
        self._last_api_call: float = 0.0
        self._oracle_index_cache: dict[str, str] | None = None

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------

    def _rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_api_call
        if elapsed < self._api_delay:
            time.sleep(self._api_delay - elapsed)
        self._last_api_call = time.monotonic()

    # ------------------------------------------------------------------
    # PIT discovery
    # ------------------------------------------------------------------

    def _pit_listing_locator(self, year: str, num: str) -> str:
        return build_consolidated_listing_locator(f"{year}/{num}")

    def _discover_pit_versions(self, year: str, num: str) -> list[str]:
        """Return available PIT amendment-id labels (e.g. ['20210680', '20230142']).

        Fetches the paginated OpenAPI consolidated collection and parses
        Finnish fin@YYYYNNNN amendment-id entries. Results are cached for
        pit_listing_max_age_days.

        Returns empty list if the statute has no PIT versions.
        """
        locator = self._pit_listing_locator(year, num)

        # Return cached listing if fresh enough
        if self._archive.has(locator, max_age_hours=self._listing_max_age_h):
            raw = self._archive.get(locator)
            if raw is not None:
                return _parse_cached_pit_listing(raw)

        # Fetch live listing via the OpenAPI consolidated collection.
        if self._verbose:
            print(f"[TransparentStore] Listing PITs: {year}/{num}", file=sys.stderr)
        try:
            versions = list_consolidated_pit_versions(year, num)
        except RuntimeError as exc:
            if self._verbose:
                print(f"[TransparentStore] Listing failed: {exc}", file=sys.stderr)
            stale = self._archive.get(locator)
            if stale is not None:
                return _parse_cached_pit_listing(stale)
            return []

        payload = ("\n".join(versions) + ("\n" if versions else "")).encode("utf-8")
        self._archive.store(locator, payload, storage_class="text")
        if self._verbose:
            print(
                f"[TransparentStore] Found {len(versions)} PIT versions for {year}/{num}",
                file=sys.stderr,
            )
        return versions

    # ------------------------------------------------------------------
    # PIT XML fetch
    # ------------------------------------------------------------------

    def _fetch_pit_xml(self, year: str, num: str, pit_version: str) -> bytes | None:
        """Fetch one PIT XML from the API using its amendment-id label. Returns None on 404."""
        # Try without /main.xml first (some API endpoints only serve the bare URL),
        # then fall back to /main.xml for endpoints that require it.
        url = f"{_API_BASE}/{year}/{num}/fin@{pit_version}"
        self._rate_limit()
        if self._verbose:
            print(f"[TransparentStore] Fetching PIT XML: {url}", file=sys.stderr)
        try:
            return _http_get_bytes(url)
        except RuntimeError as exc:
            if self._verbose:
                print(f"[TransparentStore] PIT fetch failed: {exc}", file=sys.stderr)
            return None

    # ------------------------------------------------------------------
    # Internal oracle refresh
    # ------------------------------------------------------------------

    def _cached_selected_pit(
        self,
        year: str,
        num: str,
        selector: ConsolidatedArtifactSelector | None = None,
    ) -> tuple[bytes, str] | None:
        """Return (xml_bytes, embedded_version_tag) for the selected cached PIT."""
        artifact = select_cached_consolidated_artifact(
            self._archive,
            f"{year}/{num}",
            selector=selector,
        )
        if artifact is None:
            return None
        return artifact.xml, artifact.version_tag

    def _refresh_oracle(self, sid: str) -> bytes | None:
        """Run the refresh cascade for one statute.

        Returns the best available XML bytes, or None if nothing is available.
        Side effects: stores fetched XML in the archive.
        """
        year, num = sid.split("/", 1)

        # Step 1: Select the best comparable PIT from the API collection.
        xml, best_pit = fetch_latest_consolidated(year, num)
        if xml is not None and best_pit:
            locator = oracle_url(sid, version=best_pit)

            # Already cached this exact PIT? Return it directly.
            if self._archive.has(locator):
                data = self._archive.get(locator)
                if data is not None:
                    if self._verbose:
                        print(
                            f"[TransparentStore] {sid}: using cached PIT {best_pit}",
                            file=sys.stderr,
                        )
                    return data

            store_consolidated_xml(
                self._archive,
                sid,
                xml,
                requested_locator=locator,
                storage_class="xml",
            )
            self._oracle_index_cache = None
            if self._verbose:
                print(
                    f"[TransparentStore] {sid}: stored PIT {best_pit} "
                    f"({len(xml):,} bytes)",
                    file=sys.stderr,
                )
            return xml

        # Step 3: Return stale cached PIT if available (better than nothing)
        urls = list_cached_consolidated_pit_locators(self._archive, sid)
        if urls:
            artifact = select_cached_consolidated_artifact(
                self._archive,
                sid,
                selector=ConsolidatedArtifactSelector.latest_cached_editorial(),
            )
            if artifact is not None:
                if self._verbose:
                    print(
                        f"[TransparentStore] {sid}: using stale cached PIT (no fresh available)",
                        file=sys.stderr,
                    )
                return artifact.xml

        return None

    def _fetch_source_xml(self, sid: str) -> bytes | None:
        from lawvm.corpus_store import statute_url
        from lawvm.finland.finlex_api import fetch_statute_xml

        year, num = sid.split("/", 1)
        data = fetch_statute_xml(year, num, doc_type="statute", lang_version="fin@")
        if data is not None:
            self._archive.store(statute_url(sid), data, storage_class="xml")
            if self._verbose:
                print(f"[TransparentStore] {sid}: cached source XML via API", file=sys.stderr)
        else:
            # Persist a local negative cache so the same historical miss does not
            # fan out into repeated API requests on every bench run.
            self._archive.store(_source_missing_url(sid), b"404", storage_class="text")
            if self._verbose:
                print(f"[TransparentStore] {sid}: cached missing-source marker", file=sys.stderr)
        return data

    # ------------------------------------------------------------------
    # CorpusStore interface
    # ------------------------------------------------------------------

    def read_oracle(self, sid: str) -> bytes | None:
        """Return best cached consolidated XML for sid.

        Ordinary reads are archive-only. PIT XML is treated as immutable once
        cached, so read_oracle never refreshes it opportunistically.

        The default selection route is explicit: latest cached/editorial
        consolidated artifact by embedded payload identity, not by raw path
        suffix.
        """
        cached = self.select_oracle(
            sid,
            ConsolidatedArtifactSelector.latest_cached_editorial(),
        )
        if cached is not None:
            return cached

        if self._archive.has(_oracle_missing_url(sid)):
            return None

        return None

    def select_oracle(
        self,
        sid: str,
        selector: ConsolidatedArtifactSelector,
    ) -> bytes | None:
        """Return the consolidated oracle selected by an explicit selector."""
        year, num = sid.split("/", 1)
        cached = self._cached_selected_pit(year, num, selector=selector)
        if cached is not None:
            return cached[0]
        return None

    def read_source(self, sid: str) -> bytes | None:
        """Read original enacted statute XML.

        Checks Farchive cache only. Live source fetch is reserved for
        explicit refresh_source() calls.
        Returns None immediately for IDs in the repo-managed permanent-404 list.
        """
        if sid in _KNOWN_MISSING_SOURCES:
            return None
        from lawvm.corpus_store import statute_url
        url = statute_url(sid)
        # Check archive (source/amendment XMLs live under finlex://sd/... locators)
        data = self._archive.get(url)
        if data is not None:
            return data
        if self._archive.has(_source_missing_url(sid)):
            return None

        return None

    def read_amendment(self, sid: str) -> bytes | None:
        """Read amendment act XML.

        Checks Farchive cache only. Live amendment fetch is reserved for
        explicit refresh_source() calls.
        Returns None immediately for IDs in the repo-managed permanent-404 list.
        """
        if sid in _KNOWN_MISSING_SOURCES:
            return None
        from lawvm.corpus_store import statute_url
        url = statute_url(sid)
        data = self._archive.get(url)
        if data is not None:
            return data
        if self._archive.has(_source_missing_url(sid)):
            return None

        return None

    def read_locator(self, locator: str) -> bytes | None:
        return self._archive.get(locator)

    def read_media(self, sid: str, filename: str) -> bytes | None:
        """Read media blob. Not cached in Farchive; always returns None."""
        return None

    def read_corrigendum_media(self, sid: str, filename: str) -> bytes | None:
        """Read consolidated corrigendum PDF from the corpus store."""
        urls = list_cached_corrigendum_locators(self._archive, sid, filename)
        best_data: bytes | None = None
        best_pit = -2
        for url in urls:
            m = re.search(r'/fin@(\d+)/', url)
            pit_key = int(m.group(1)) if m else -1
            if pit_key > best_pit:
                data = self._archive.get(url)
                if data is not None:
                    best_pit = pit_key
                    best_data = data
        return best_data

    def list_statute_ids(self) -> list[str]:
        """List all statute IDs present in the Farchive (source XMLs)."""
        sids: list[str] = []
        for locator in self._archive.locators("finlex://sd/%/fin/main.xml"):
            m = re.match(r'finlex://sd/(\d{4}/[^/]+)/fin/main\.xml$', locator)
            if m:
                sids.append(m.group(1))
        return sids

    def oracle_path_index(self, **kwargs: object) -> dict[str, str]:
        """Return {sid -> best oracle identifier}.

        Embedded XML identity wins over the path suffix when both exist.
        """
        selector = kwargs.get("selector")
        if (
            selector is None
            or selector == ConsolidatedArtifactSelector.latest_cached_editorial()
        ):
            if self._oracle_index_cache is not None:
                return self._oracle_index_cache
            result = best_cached_consolidated_path_index(self._archive)
            self._oracle_index_cache = dict(result)
            return self._oracle_index_cache

        return select_cached_consolidated_path_index(
            self._archive,
            selector=cast(ConsolidatedArtifactSelector, selector),
        )

    def close(self) -> None:
        self._archive.close()

    # ------------------------------------------------------------------
    # Refresh one statute
    # ------------------------------------------------------------------

    def refresh(self, sid: str, force: bool = False) -> bytes | None:
        """Force-refresh the oracle for one statute from the API.

        Args:
            sid:   Statute ID e.g. "2002/738".
            force: If True, bypass the fresh-cache check and always re-fetch.
                   If False, behave like read_oracle (return cache if fresh).

        Returns XML bytes, or None if unavailable.
        """
        if force:
            # Force mode bypasses the fresh-cache shortcut and re-fetches from API.
            return self._refresh_oracle(sid)
        cached = self.read_oracle(sid)
        if cached is not None:
            return cached
        return self._refresh_oracle(sid)

    def refresh_source(self, sid: str) -> bytes | None:
        """Fetch one source/amendment XML into the archive explicitly."""
        if sid in _KNOWN_MISSING_SOURCES:
            return None
        return self._fetch_source_xml(sid)

    # ------------------------------------------------------------------
    # Refresh stale statutes
    # ------------------------------------------------------------------

    def refresh_stale(
        self,
        max_age_days: float = 30.0,
        limit: int | None = None,
        verbose: bool = False,
    ) -> dict[str, int]:
        """Find all cached oracles older than max_age_days and refresh them.

        Args:
            max_age_days: Oracles older than this are refreshed.
            limit: If set, stop after this many refreshes (useful for testing).
            verbose: Print per-statute results.

        Returns:
            {"refreshed": N, "unchanged": N, "failed": N}
        """
        max_age_h = max_age_days * 24.0
        urls = list_cached_consolidated_locators(self._archive)

        # Find stale SIDs
        stale_sids: list[str] = []
        for url in urls:
            parts = parse_versioned_consolidated_main_locator(url)
            if parts is None:
                continue
            if not self._archive.has(url, max_age_hours=max_age_h):
                stale_sids.append(parts.sid)

        # Deduplicate (a SID may have multiple PIT URLs in archive)
        unique_stale = sorted(set(stale_sids))

        if limit is not None:
            unique_stale = unique_stale[:limit]

        stats: dict[str, int] = {"refreshed": 0, "unchanged": 0, "failed": 0}

        for i, sid in enumerate(unique_stale):
            if verbose:
                print(
                    f"[TransparentStore] refresh_stale [{i+1}/{len(unique_stale)}]: {sid}",
                    file=sys.stderr,
                )
            try:
                result = self._refresh_oracle(sid)
                if result is not None:
                    stats["refreshed"] += 1
                else:
                    stats["failed"] += 1
            except RuntimeError as exc:
                if verbose:
                    print(f"  ERROR: {exc}", file=sys.stderr)
                stats["failed"] += 1

        return stats

    # ------------------------------------------------------------------
    # Freshness report
    # ------------------------------------------------------------------

    def freshness_report(
        self,
        sids: list[str] | None = None,
        check_html: bool = False,
        html_cache_path: str | Path | None = None,
    ) -> FreshnessReport:
        """Generate per-statute freshness report.

        Args:
            sids: Statute IDs to report on. Defaults to all in list_statute_ids().
            check_html: If True, also fetch HTML section count for comparison
                        (slow — makes one HTML request per stale statute).
            html_cache_path: Path to HTML cache DB (passed to finlex_html).

        Returns:
            FreshnessReport with per-statute StatuteFreshness rows.
        """
        if sids is None:
            sids = self.list_statute_ids()

        report = FreshnessReport(total=len(sids))

        for sid in sids:
            year, num = sid.split("/", 1)

            best_record = self._cached_selected_pit(
                year,
                num,
                ConsolidatedArtifactSelector.latest_cached_editorial(),
            )
            best_pit_url = ""
            best_pit_str = ""
            best_pit_data: bytes | None = None
            if best_record is not None:
                best_pit_data, best_pit_str = best_record
                for url in list_cached_consolidated_pit_locators(self._archive, sid):
                    if self._archive.get(url) == best_pit_data:
                        best_pit_url = url
                        break

            # Get last_seen timestamp
            last_seen = ""
            age_days = -1.0
            source = "not_found"

            if best_pit_url:
                # Get most recent observation timestamp from Farchive locator_span table.
                # last_confirmed_at is stored as milliseconds since Unix epoch.
                row = self._archive._conn.execute(
                    "SELECT MAX(last_confirmed_at) FROM locator_span WHERE locator=?",
                    (best_pit_url,),
                ).fetchone()
                if row and row[0]:
                    epoch_ms = row[0]
                    # Convert ms epoch to ISO timestamp string
                    from datetime import timezone
                    dt = datetime.fromtimestamp(epoch_ms / 1000.0, tz=timezone.utc)
                    last_seen = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                    age_days = _age_days(last_seen)
                    source = "api_pit"

            # Section count from XML oracle
            xml_section_count: int | None = None
            if best_pit_data is not None:
                # Count <section> elements as proxy for section count
                xml_section_count = best_pit_data.count(b'<section ')

            # Section count from HTML (freshness check)
            html_section_count: int | None = None
            if check_html and source == "api_pit":
                try:
                    from lawvm.finland.finlex_html import html_section_count as _hsc
                    html_section_count = _hsc(
                        year, num,
                        cache_path=html_cache_path,
                    )
                except (NameError, TypeError, AttributeError):
                    raise  # programming bugs — fail loud
                except Exception:
                    html_section_count = None

            # Determine staleness
            stale = False
            if source == "not_found":
                stale = True
            elif age_days > _PIT_XML_MAX_AGE_DAYS:
                stale = True
            elif (
                html_section_count is not None
                and xml_section_count is not None
                and html_section_count > xml_section_count
            ):
                stale = True

            row_obj = StatuteFreshness(
                sid=sid,
                source=source,
                pit_version=best_pit_str,
                last_seen=last_seen,
                age_days=round(age_days, 1),
                section_count_xml=xml_section_count,
                section_count_html=html_section_count,
                stale=stale,
            )
            report.rows.append(row_obj)

            if source == "not_found":
                report.not_found += 1
            elif stale:
                report.stale += 1
            else:
                report.fresh += 1

            if not best_pit_str:
                report.no_pit += 1

        return report
