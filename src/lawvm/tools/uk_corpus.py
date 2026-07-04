"""uk_corpus.py — native ``lawvm uk-corpus`` acquisition and curation.

Acquires the UK legislation corpus into a Farchive. Single, fully resumable,
idempotent pipeline: only fetches what is missing or stale. This is the native
CLI home for UK corpus sync (harmonized with ``ee-corpus`` and ``nz-corpus``),
reachable as ``lawvm uk-corpus <subcommand>``.

Subcommands (``lawvm uk-corpus <sub>``):
  acquire    enumerate primary acts via CSV and download enacted/current/effects
  affecting  fetch enacted XML for affecting acts discovered in effects feeds
  refresh    re-fetch mutable resources (current XML + effects feeds) if stale
  repair-multiple-choices
             resolve cached Multiple Choices markers into leaf source locators
  stats      archive summary
  train-dict / repack   compression maintenance
  all        acquire + affecting + refresh + repair-multiple-choices

Immutability model:
  Enacted/affecting XML  IMMUTABLE  store once (skip if digest present)
  Current XML / effects  SLOW (TTL) re-fetch if last_confirmed > TTL
  CSV enumeration        EPHEMERAL  fetch on demand, never stored

Rate limiting: default 0.3 s between requests; HTTP 429 → backoff (respects
Retry-After); 404/410 → recorded permanent miss, never retried.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import gzip
import re
import sys
import time
import xml.etree.ElementTree as ET
import zlib
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from farchive import CompressionPolicy, Farchive

from lawvm.corpus_store import validate_farchive_create_path
from lawvm.core.http_identity import LAWVM_USER_AGENT
from lawvm.uk_legislation.source_state import (
    UKSourceStatus,
    classify_uk_source_blob,
    fetch_uk_multiple_choice_candidate_sources,
    uk_multiple_choice_candidate_data_urls,
)
from lawvm.core.quirks_disposition import QuirksDisposition

_LEG_BASE = "https://www.legislation.gov.uk"
_USER_AGENT = LAWVM_USER_AGENT

# Primary act types (enumerated via CSV feeds)
PRIMARY_TYPES = ["ukpga", "asp", "asc", "nia", "eur"]

# Secondary types that appear as affecting acts in effects feeds
SECONDARY_TYPES = frozenset(
    [
        "ukpga", "uksi", "asp", "asc", "nia", "nisi", "ssi", "wsi", "mnia",
        "apni", "ukci", "eur", "ukla", "anaw", "nisr", "mwa", "eudn",
    ]
)

_DEFAULT_DELAY = 0.3  # seconds between requests
_CSV_PAGE_SIZE = 500
_MAX_RETRIES = 3
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

_TTL_CURRENT = 300 * 86400  # ~30 days
_TTL_EFFECTS = 300 * 86400

_DEFAULT_ARCHIVE = Path(__file__).resolve().parents[3] / "data" / "uk_legislation.farchive"
UKActRow = dict[str, str]


def _missing_enacted_locator(act_id: str) -> str:
    """Negative-cache locator for permanently missing affecting-act enacted XML."""
    return f"leg://missing/uk/{act_id}/enacted/data.xml"


def _affecting_acquisition_event(
    *, affecting_act_id: str, url: str, acquisition_status: str, rule_id: str, reason: str, blocking: bool
) -> dict[str, object]:
    return {
        "rule_id": rule_id,
        "phase": "acquisition",
        "family": "source_pathology",
        "affecting_act_id": affecting_act_id,
        "locator": _missing_enacted_locator(affecting_act_id),
        "url": url,
        "acquisition_status": acquisition_status,
        "reason": reason,
        "blocking": blocking,
        "strict_disposition": "block" if blocking else "record",
        "quirks_disposition": QuirksDisposition.RECORD,
    }


def _decode_content_encoding(data: bytes, content_encoding: Optional[str]) -> bytes:
    """Decompress an HTTP body per its ``Content-Encoding``.

    We advertise ``Accept-Encoding: gzip, deflate``, so the server may return a
    compressed body.  ``urllib`` does not auto-decompress, so the raw bytes must
    be decoded before they are stored — otherwise the archive holds gzip bytes
    that no XML parser can read (a corpus-corruption bug).
    """
    encoding = (content_encoding or "").strip().lower()
    if not encoding or encoding == "identity":
        return data
    if encoding == "gzip":
        return gzip.decompress(data)
    if encoding == "deflate":
        try:
            return zlib.decompress(data)
        except zlib.error:
            return zlib.decompress(data, -zlib.MAX_WBITS)
    return data


def _is_storable_xml(data: bytes) -> bool:
    """True if *data* looks like XML text (not gzip/zlib bytes or an error page).

    A defensive guard against the corpus-corruption class where a compressed or
    non-XML body reaches the archive under an ``xml`` storage class.  Real
    legislation XML begins with ``<`` after an optional BOM/whitespace; gzip
    (``1f 8b``) and zlib (``78 xx``) bodies do not.
    """
    head = data.lstrip(b"\xef\xbb\xbf \t\r\n")
    lower_head = head[:64].lower()
    if lower_head.startswith((b"<!doctype html", b"<html")):
        return False
    return head[:1] == b"<"


class _HTTP:
    """Rate-limited fetcher with retry/backoff."""

    def __init__(self, delay: float = _DEFAULT_DELAY):
        self.delay = delay
        self._last = 0.0
        self.requests = 0
        self.bytes = 0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)

    def get_with_status(self, url: str) -> tuple[Optional[bytes], Optional[int]]:
        self._throttle()
        attempt = 0
        while attempt <= _MAX_RETRIES:
            attempt += 1
            self._last = time.monotonic()
            self.requests += 1
            req = Request(url)
            req.add_header("User-Agent", _USER_AGENT)
            req.add_header("Accept-Encoding", "gzip, deflate")
            try:
                with urlopen(req, timeout=60) as resp:
                    data = _decode_content_encoding(
                        resp.read(), resp.headers.get("Content-Encoding")
                    )
                    self.bytes += len(data)
                    return data, resp.getcode()
            except HTTPError as e:
                if e.code == 300:
                    data = _decode_content_encoding(
                        e.read(), e.headers.get("Content-Encoding")
                    )
                    self.bytes += len(data)
                    return data, e.code
                if e.code in (404, 410):
                    return None, e.code
                if e.code in _RETRYABLE_STATUS:
                    backoff = min(2**attempt * 0.5, 30)
                    ra = e.headers.get("Retry-After")
                    if ra:
                        try:
                            backoff = max(float(ra), backoff)
                        except ValueError:
                            pass
                        time.sleep(backoff)
                    continue
                return None, e.code
            except URLError:
                if attempt <= _MAX_RETRIES:
                    time.sleep(min(2**attempt * 0.5, 15))
                    continue
                return None, None
            except Exception:
                if attempt <= _MAX_RETRIES:
                    time.sleep(min(2**attempt * 0.5, 15))
                    continue
                return None, None
        return None, None

    def get(self, url: str) -> Optional[bytes]:
        data, _ = self.get_with_status(url)
        return data


def _is_stale(archive: Farchive, url: str, ttl: float) -> bool:
    spans = archive.history(url)
    if not spans:
        return True
    last = spans[-1].last_confirmed_at
    if last is None:
        return True
    return (time.time() - last.timestamp()) > ttl


def _parse_csv_acts(act_type: str, data: bytes) -> list[UKActRow]:
    text = data.decode("utf-8-sig", errors="replace")
    acts: list[UKActRow] = []
    for row in csv.DictReader(io.StringIO(text)):
        year = (row.get("YEAR") or row.get("Year") or "").strip()
        num = (row.get("NUMBER") or row.get("Number") or "").strip()
        title = (row.get("TITLE") or row.get("Title") or "").strip()
        if year and num:
            acts.append({"type": act_type, "year": year, "num": num, "title": title})
    return acts


def _enumerate_type(act_type: str, http: _HTTP) -> list[UKActRow]:
    all_acts: list[UKActRow] = []
    page = 1
    while True:
        url = f"{_LEG_BASE}/{act_type}/data.csv?results-count={_CSV_PAGE_SIZE}&page={page}"
        data = http.get(url)
        if not data:
            break
        acts = _parse_csv_acts(act_type, data)
        all_acts.extend(acts)
        if len(acts) < _CSV_PAGE_SIZE:
            break
        page += 1
    return all_acts


def _store_if_new(archive: Farchive, url: str, data: bytes, sc: str = "xml") -> bool:
    if sc == "xml" and not _is_storable_xml(data):
        print(
            f"  [guard] refusing non-XML payload for {url} "
            f"(first bytes {data[:4]!r}); not stored",
            file=sys.stderr,
        )
        return False
    spans = archive.history(url)
    if spans:
        digest = hashlib.sha256(data).hexdigest()
        if spans[-1].digest == digest:
            return False
    archive.store(url, data, storage_class=sc)
    return True


def _source_xml_status(data: bytes | None) -> UKSourceStatus:
    return classify_uk_source_blob(data).source_state_status


def _cached_source_xml_status(archive: Farchive, url: str) -> UKSourceStatus:
    if not archive.has(url):
        return UKSourceStatus.ABSENT
    return _source_xml_status(archive.get(url))


def _source_xml_fetch_error(data: bytes | None, http_status: int | None) -> str | None:
    if not data:
        return f"http_{http_status}" if http_status is not None else "transport_error"
    source_status = _source_xml_status(data)
    if source_status is not UKSourceStatus.AVAILABLE:
        return source_status.value
    if not _is_storable_xml(data):
        return "non_xml"
    return None


def _store_source_xml_if_available(
    archive: Farchive,
    url: str,
    data: bytes,
) -> bool:
    source_error = _source_xml_fetch_error(data, 200)
    if source_error is not None:
        print(
            f"  [source-frontier] refusing {source_error} source payload for {url}; "
            "not stored as XML",
            file=sys.stderr,
        )
        return False
    return _store_if_new(archive, url, data, "xml")


def _fetch_multiple_choice_candidate_sources(
    archive: Farchive,
    http: _HTTP,
    blob: bytes | None,
    *,
    include_current: bool,
    include_enacted: bool,
    source_url: str = "",
) -> int:
    def cached_available(url: str) -> bool:
        return _cached_source_xml_status(archive, url) is UKSourceStatus.AVAILABLE

    return fetch_uk_multiple_choice_candidate_sources(
        blob,
        include_current=include_current,
        include_enacted=include_enacted,
        source_url=source_url,
        cached_available=cached_available,
        fetch=http.get_with_status,
        source_error=_source_xml_fetch_error,
        store_available=lambda url, data: _store_source_xml_if_available(
            archive,
            url,
            data,
        ),
        unresolved_nested_multiple_choice=lambda url: print(
            f"  [source-frontier] nested Multiple Choices candidate {url}; not stored",
            file=sys.stderr,
        ),
    )


def _available_candidate_source_count(
    archive: Farchive,
    candidate_urls: tuple[str, ...],
) -> int:
    return sum(
        1
        for url in candidate_urls
        if _cached_source_xml_status(archive, url) is UKSourceStatus.AVAILABLE
    )


def _fetch_effects_pages(
    act_type: str, year: str, number: str, archive: Farchive, http: _HTTP, *, force: bool = False
) -> int:
    base = f"{_LEG_BASE}/changes/affected/{act_type}/{year}/{number}/data.feed"
    p1_url = f"{base}?results-count=50&sort=modified"
    ns = "{http://www.legislation.gov.uk/namespaces/legislation}totalPages"

    if not force and not _is_stale(archive, p1_url, _TTL_EFFECTS):
        data = archive.get(p1_url)
        if data:
            try:
                root = ET.fromstring(data)
                el = root.find(f".//{ns}")
                return int(el.text) if el is not None and el.text else 1
            except Exception:
                return 1
        return 0

    data = http.get(p1_url)
    if not data:
        return 0
    _store_if_new(archive, p1_url, data, "xml")
    total_pages = 1
    try:
        root = ET.fromstring(data)
        el = root.find(f".//{ns}")
        total_pages = int(el.text) if el is not None and el.text else 1
    except Exception:
        pass
    for p in range(2, total_pages + 1):
        purl = f"{p1_url}&page={p}"
        if not force and not _is_stale(archive, purl, _TTL_EFFECTS):
            continue
        pdata = http.get(purl)
        if pdata:
            _store_if_new(archive, purl, pdata, "xml")
    return total_pages


def _scan_affecting_acts(archive: Farchive) -> set[str]:
    ns = "http://www.legislation.gov.uk/namespaces/metadata"
    affecting: set[str] = set()
    for loc in archive.locators("%/data.feed%"):
        data = archive.get(loc)
        if not data:
            continue
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            continue
        for eff in root.findall(f".//{{{ns}}}Effect"):
            uri = eff.get("AffectingURI", "")
            m = re.search(r"/([a-z]+)/(\d{4})/(\d+)", uri)
            if m and m.group(1) in SECONDARY_TYPES:
                affecting.add(f"{m.group(1)}/{m.group(2)}/{m.group(3)}")
    return affecting


def _split_statute_id(statute_id: str) -> tuple[str, str, str]:
    parts = statute_id.strip("/").split("/")
    if len(parts) != 3 or not all(parts):
        raise ValueError(f"invalid UK statute id: {statute_id!r}")
    return parts[0], parts[1], parts[2]


# ── Phases ──────────────────────────────────────────────────────────────────


def do_enumerate(types: list[str], http: _HTTP) -> dict[str, list[UKActRow]]:
    manifest: dict[str, list[UKActRow]] = {}
    total = 0
    for t in types:
        acts = _enumerate_type(t, http)
        manifest[t] = acts
        total += len(acts)
        print(f"  {t}: {len(acts):,} acts")
    print(f"  Total: {total:,} acts")
    return manifest


def do_download(
    manifest: dict[str, list[UKActRow]], archive: Farchive, http: _HTTP, *, enacted_only: bool = False
) -> dict[str, int]:
    all_acts = [(t, a) for t, acts in manifest.items() for a in acts]
    total = len(all_acts)
    n_enacted = n_current = n_effects = n_multiple_choices = n_candidate_sources = 0
    for i, (_, act) in enumerate(all_acts, 1):
        t, y, n = act["type"], act["year"], act["num"]
        base = f"{_LEG_BASE}/{t}/{y}/{n}"
        enacted_url = f"{base}/enacted/data.xml"
        enacted_cached_status = _cached_source_xml_status(archive, enacted_url)
        if enacted_cached_status in {UKSourceStatus.ABSENT, UKSourceStatus.MULTIPLE_CHOICES}:
            data, status = http.get_with_status(enacted_url)
            source_error = _source_xml_fetch_error(data, status)
            if data and source_error == UKSourceStatus.MULTIPLE_CHOICES.value:
                n_multiple_choices += 1
                n_candidate_sources += _fetch_multiple_choice_candidate_sources(
                    archive,
                    http,
                    data,
                    include_current=not enacted_only,
                    include_enacted=True,
                    source_url=enacted_url,
                )
            elif data and source_error is None and _store_source_xml_if_available(
                archive,
                enacted_url,
                data,
            ):
                n_enacted += 1
        if not enacted_only:
            current_url = f"{base}/data.xml"
            current_cached_status = _cached_source_xml_status(archive, current_url)
            if (
                current_cached_status is UKSourceStatus.MULTIPLE_CHOICES
                or _is_stale(archive, current_url, _TTL_CURRENT)
            ):
                data, status = http.get_with_status(current_url)
                source_error = _source_xml_fetch_error(data, status)
                if data and source_error == UKSourceStatus.MULTIPLE_CHOICES.value:
                    n_multiple_choices += 1
                    n_candidate_sources += _fetch_multiple_choice_candidate_sources(
                        archive,
                        http,
                        data,
                        include_current=True,
                        include_enacted=True,
                        source_url=current_url,
                    )
                elif data and source_error is None and _store_source_xml_if_available(
                    archive,
                    current_url,
                    data,
                ):
                    n_current += 1
            if _fetch_effects_pages(t, y, n, archive, http) > 0:
                n_effects += 1
        if i % 500 == 0 or i == total:
            st = archive.stats()
            print(
                f"  [{i:,}/{total:,}]  enacted+{n_enacted:,}  current+{n_current:,}  "
                f"effects+{n_effects:,}  multi={n_multiple_choices:,}  "
                f"candidate_sources+{n_candidate_sources:,}  "
                f"archive={st.locator_count:,} locators  last={t}/{y}/{n}"
            )
    return {
        "enacted": n_enacted,
        "current": n_current,
        "effects": n_effects,
        "multiple_choices": n_multiple_choices,
        "candidate_sources": n_candidate_sources,
    }


def do_affecting(
    archive: Farchive, http: _HTTP, *, types: Optional[set[str]] = None,
    diagnostics_out: Optional[list[dict[str, object]]] = None,
) -> dict[str, int]:
    affecting = _scan_affecting_acts(archive)
    if types:
        affecting = {a for a in affecting if a.split("/")[0] in types}
    to_fetch = [
        a for a in sorted(affecting)
        if (
            _cached_source_xml_status(archive, f"{_LEG_BASE}/{a}/enacted/data.xml")
            is not UKSourceStatus.AVAILABLE
        )
        and (not archive.has(_missing_enacted_locator(a)))
    ]
    print(f"  {len(affecting) - len(to_fetch):,} cached, {len(to_fetch):,} to fetch")
    n_ok = n_fail = n_404 = 0
    for i, aid in enumerate(to_fetch, 1):
        url = f"{_LEG_BASE}/{aid}/enacted/data.xml"
        data, status = http.get_with_status(url)
        source_error = _source_xml_fetch_error(data, status)
        if data and source_error == UKSourceStatus.MULTIPLE_CHOICES.value:
            _fetch_multiple_choice_candidate_sources(
                archive,
                http,
                data,
                include_current=False,
                include_enacted=True,
                source_url=url,
            )
            n_fail += 1
            if diagnostics_out is not None:
                diagnostics_out.append(_affecting_acquisition_event(
                    affecting_act_id=aid, url=url, acquisition_status="ambiguous",
                    rule_id="uk_acquire_affecting_enacted_multiple_choices",
                    reason="multiple_choices", blocking=True))
        elif data and source_error is None and _store_source_xml_if_available(archive, url, data):
            n_ok += 1
        elif status in {404, 410}:
            archive.store(_missing_enacted_locator(aid), b"404", storage_class="text")
            n_404 += 1
            if diagnostics_out is not None:
                diagnostics_out.append(_affecting_acquisition_event(
                    affecting_act_id=aid, url=url, acquisition_status="permanent_missing_cached",
                    rule_id="uk_acquire_affecting_enacted_permanent_missing",
                    reason=f"http_{status}", blocking=False))
        else:
            n_fail += 1
            if diagnostics_out is not None:
                diagnostics_out.append(_affecting_acquisition_event(
                    affecting_act_id=aid, url=url, acquisition_status="error",
                    rule_id="uk_acquire_affecting_enacted_fetch_failed",
                    reason=source_error or (
                        f"http_{status}" if status is not None else "transport_error"
                    ),
                    blocking=True))
        if i % 1000 == 0 or i == len(to_fetch):
            print(f"  [{i:,}/{len(to_fetch):,}]  ok={n_ok:,}  fail={n_fail:,}  404={n_404:,}  last={aid}")
    return {"fetched": n_ok, "failed": n_fail, "gone": n_404}


def do_refresh(
    archive: Farchive, http: _HTTP, *, statute_ids: Optional[set[str]] = None, force: bool = False
) -> dict[str, int]:
    n_current = n_effects = 0
    if statute_ids:
        for sid in sorted(statute_ids):
            act_type, year, number = _split_statute_id(sid)
            current_url = f"{_LEG_BASE}/{act_type}/{year}/{number}/data.xml"
            cached_status = _cached_source_xml_status(archive, current_url)
            if force or cached_status is UKSourceStatus.MULTIPLE_CHOICES or _is_stale(
                archive,
                current_url,
                _TTL_CURRENT,
            ):
                data, status = http.get_with_status(current_url)
                source_error = _source_xml_fetch_error(data, status)
                if data and source_error == UKSourceStatus.MULTIPLE_CHOICES.value:
                    _fetch_multiple_choice_candidate_sources(
                        archive,
                        http,
                        data,
                        include_current=True,
                        include_enacted=True,
                        source_url=current_url,
                    )
                elif data and source_error is None and _store_source_xml_if_available(
                    archive,
                    current_url,
                    data,
                ):
                    n_current += 1
            if _fetch_effects_pages(act_type, year, number, archive, http, force=force) > 0:
                n_effects += 1
        return {"current": n_current, "effects": n_effects}
    for loc in archive.locators("%/data.xml"):
        if "/enacted/" in loc:
            continue
        cached_status = _cached_source_xml_status(archive, loc)
        if cached_status is UKSourceStatus.MULTIPLE_CHOICES or _is_stale(archive, loc, _TTL_CURRENT):
            data, status = http.get_with_status(loc)
            source_error = _source_xml_fetch_error(data, status)
            if data and source_error == UKSourceStatus.MULTIPLE_CHOICES.value:
                _fetch_multiple_choice_candidate_sources(
                    archive,
                    http,
                    data,
                    include_current=True,
                    include_enacted=True,
                    source_url=loc,
                )
            elif data and source_error is None:
                _store_source_xml_if_available(archive, loc, data)
                n_current += 1
    for loc in archive.locators("%/data.feed%"):
        if _is_stale(archive, loc, _TTL_EFFECTS):
            data = http.get(loc)
            if data:
                _store_if_new(archive, loc, data, "xml")
                n_effects += 1
    return {"current": n_current, "effects": n_effects}


def _locator_matches_statute_ids(locator: str, statute_ids: set[str] | None) -> bool:
    if not statute_ids:
        return True
    return any(f"/{sid.strip('/')}/" in locator for sid in statute_ids)


def _multiple_choice_base_locator(locator: str) -> str:
    if locator.endswith("/enacted/data.xml"):
        return locator.removesuffix("/enacted/data.xml")
    return locator.removesuffix("/data.xml")


def _multiple_choice_manifest_locator(base_locator: str) -> str:
    digest = hashlib.sha256(base_locator.encode("utf-8")).hexdigest()[:24]
    return f"leg://source-frontier/uk/multiple-choices/{digest}.json"


def _store_multiple_choice_manifest(
    archive: Farchive,
    *,
    base_locator: str,
    ambiguity_locators: Iterable[str],
    candidate_urls: Iterable[str],
) -> None:
    urls = tuple(sorted({url for url in candidate_urls if url}))
    if not urls:
        return
    available = _available_candidate_source_count(archive, urls)
    payload = {
        "schema": "lawvm.uk.multiple_choice_candidate_manifest.v1",
        "truth_claim": "candidate_leaf_witnesses_not_source_selection",
        "base_locator": base_locator,
        "ambiguity_locators": sorted({loc for loc in ambiguity_locators if loc}),
        "candidate_urls": list(urls),
        "candidate_source_count": len(urls),
        "candidate_sources_available": available,
        "source_selection_claims": False,
        "replay_claims": False,
        "safe_default": "keep_ambiguous_locator_non_authoritative",
        "forbidden_shortcuts": [
            "multiple_choice_first_candidate_as_source_truth",
            "candidate_leaf_presence_as_replay_authorization",
            "ambiguous_locator_as_replay_source",
        ],
    }
    archive.store(
        _multiple_choice_manifest_locator(base_locator),
        json.dumps(payload, sort_keys=True).encode("utf-8"),
        storage_class="json",
    )


def _load_multiple_choice_manifest_candidate_urls(
    archive: Farchive,
    base_locator: str,
) -> tuple[str, ...]:
    locator = _multiple_choice_manifest_locator(base_locator)
    if not archive.has(locator):
        return ()
    try:
        raw = archive.get(locator)
        if raw is None:
            return ()
        payload = json.loads(raw.decode("utf-8"))
    except (AttributeError, json.JSONDecodeError):
        return ()
    values = payload.get("candidate_urls") if isinstance(payload, dict) else None
    if not isinstance(values, list):
        return ()
    return tuple(str(value) for value in values if str(value))


def do_repair_multiple_choices(
    archive: Farchive,
    http: _HTTP,
    *,
    statute_ids: Optional[set[str]] = None,
    limit: int = 0,
) -> dict[str, int]:
    """Fetch candidate leaf XML for cached UK Multiple Choices source locators.

    Older corpus fetches stored short ``HTTP 300 Multiple Choices`` bodies under
    XML locators.  This repair keeps those ambiguous locators non-authoritative:
    it refetches the ambiguity page only to discover candidate leaf URLs, then
    stores the leaf XML under each candidate's actual legislation.gov.uk URL.
    """
    candidate_locators = [
        loc
        for loc in archive.locators("%/data.xml")
        if _locator_matches_statute_ids(loc, statute_ids)
        and _cached_source_xml_status(archive, loc) is UKSourceStatus.MULTIPLE_CHOICES
    ]
    if limit > 0:
        candidate_locators = candidate_locators[:limit]
    grouped: dict[str, list[str]] = {}
    for loc in candidate_locators:
        grouped.setdefault(_multiple_choice_base_locator(loc), []).append(loc)
    groups = [(base, sorted(locs)) for base, locs in sorted(grouped.items())]
    print(
        f"  found {len(candidate_locators):,} cached Multiple Choices locator(s) "
        f"across {len(groups):,} ambiguous base(s)",
        flush=True,
    )
    n_repaired = n_candidate_sources = n_direct_sources = n_no_candidates = n_failed = 0
    n_candidate_source_urls = n_candidate_sources_available = 0
    for i, (_base, locs) in enumerate(groups, 1):
        last_loc = locs[-1]
        group_failed = True
        group_no_candidates = False
        group_repaired = False
        for loc in locs:
            last_loc = loc
            data, status = http.get_with_status(loc)
            source_error = _source_xml_fetch_error(data, status)
            if data and source_error is None:
                if _store_source_xml_if_available(archive, loc, data):
                    n_direct_sources += 1
                group_repaired = True
                group_failed = False
                continue
            if not data or source_error != UKSourceStatus.MULTIPLE_CHOICES.value:
                continue
            group_failed = False
            candidate_urls = uk_multiple_choice_candidate_data_urls(
                data,
                include_current=True,
                include_enacted=True,
            )
            if not candidate_urls:
                group_no_candidates = True
                continue
            n_candidate_source_urls += len(candidate_urls)
            n_candidate_sources += _fetch_multiple_choice_candidate_sources(
                archive,
                http,
                data,
                include_current=True,
                include_enacted=True,
                source_url=loc,
            )
            n_candidate_sources_available += _available_candidate_source_count(
                archive,
                candidate_urls,
            )
            _store_multiple_choice_manifest(
                archive,
                base_locator=_multiple_choice_base_locator(loc),
                ambiguity_locators=locs,
                candidate_urls=candidate_urls,
            )
            group_repaired = True
            break
        if group_failed:
            n_failed += 1
        elif group_repaired:
            n_repaired += 1
        elif group_no_candidates:
            n_no_candidates += 1
        if i % 10 == 0 or i == len(groups):
            print(
                f"  [{i:,}/{len(groups):,}]  repaired={n_repaired:,}  "
                f"candidate_urls={n_candidate_source_urls:,}  "
                f"candidate_sources_available={n_candidate_sources_available:,}  "
                f"candidate_sources+{n_candidate_sources:,}  direct_sources+{n_direct_sources:,}  "
                f"no_candidates={n_no_candidates:,}  failed={n_failed:,}  last={last_loc}",
                flush=True,
            )
    return {
        "ambiguous_locators": len(candidate_locators),
        "ambiguous_groups": len(groups),
        "repaired_locators": n_repaired,
        "candidate_source_urls": n_candidate_source_urls,
        "candidate_sources_available": n_candidate_sources_available,
        "candidate_sources": n_candidate_sources,
        "direct_sources": n_direct_sources,
        "no_candidates": n_no_candidates,
        "failed": n_failed,
    }


def _multiple_choice_candidate_source_summary(archive: Farchive) -> dict[str, int]:
    """Summarize whether cached ambiguity markers have fetched leaf witnesses.

    Multiple Choices locators remain non-authoritative even when every leaf is
    cached.  This summary is acquisition evidence only: it answers whether the
    candidate source witnesses are present without selecting any leaf for replay.
    """
    grouped: dict[str, set[str]] = {}
    no_candidate_groups: set[str] = set()
    ambiguous_locators = 0
    for loc in archive.locators("%/data.xml"):
        if _cached_source_xml_status(archive, loc) is not UKSourceStatus.MULTIPLE_CHOICES:
            continue
        ambiguous_locators += 1
        blob = archive.get(loc)
        base = _multiple_choice_base_locator(loc)
        candidate_urls = set(
            uk_multiple_choice_candidate_data_urls(
                blob,
                include_current=True,
                include_enacted=True,
            )
        )
        if not candidate_urls:
            candidate_urls.update(
                _load_multiple_choice_manifest_candidate_urls(archive, base)
            )
        if not candidate_urls:
            no_candidate_groups.add(base)
            grouped.setdefault(base, set())
            continue
        grouped.setdefault(base, set()).update(candidate_urls)

    candidate_source_urls = 0
    candidate_sources_available = 0
    groups_with_candidates = 0
    groups_fully_available = 0
    groups_partially_available = 0
    groups_without_available_candidates = 0
    for urls in grouped.values():
        if not urls:
            continue
        groups_with_candidates += 1
        available = _available_candidate_source_count(archive, tuple(sorted(urls)))
        candidate_source_urls += len(urls)
        candidate_sources_available += available
        if available == len(urls):
            groups_fully_available += 1
        elif available > 0:
            groups_partially_available += 1
        else:
            groups_without_available_candidates += 1

    return {
        "ambiguous_locators": ambiguous_locators,
        "ambiguous_groups": len(grouped),
        "groups_with_candidates": groups_with_candidates,
        "candidate_source_urls": candidate_source_urls,
        "candidate_sources_available": candidate_sources_available,
        "groups_fully_available": groups_fully_available,
        "groups_partially_available": groups_partially_available,
        "groups_without_available_candidates": groups_without_available_candidates,
        "groups_without_candidate_urls": len(no_candidate_groups),
    }


def do_stats(archive: Farchive) -> None:
    st = archive.stats()
    print(f"\n{'=' * 60}\nArchive: {st.db_path}\n{'=' * 60}")
    print(f"  Locators:     {st.locator_count:,}")
    print(f"  Blobs:        {st.blob_count:,}")
    print(f"  Raw:          {st.total_raw_bytes / 1e6:.1f} MB")
    print(f"  Stored:       {st.total_stored_bytes / 1e6:.1f} MB")
    print(f"  Compression:  {(st.compression_ratio or 0.0):.1f}x")
    cats: Counter[str] = Counter()
    for loc in archive.locators("%"):
        if "/data.csv" in loc:
            cats["csv"] += 1
        elif "/enacted/data.xml" in loc:
            cats["enacted"] += 1
        elif "/data.feed" in loc:
            cats["effects"] += 1
        elif "/data.xml" in loc:
            cats["current"] += 1
        else:
            cats["other"] += 1
    for k, v in sorted(cats.items()):
        print(f"    {k:10s}: {v:,}")
    source_states: Counter[str] = Counter()
    for loc in archive.locators("%/data.xml"):
        source_states[_cached_source_xml_status(archive, loc).value] += 1
    if source_states:
        print(
            "  Source states: "
            + ", ".join(f"{k}={v:,}" for k, v in sorted(source_states.items()))
        )
    multiple_choice_summary = _multiple_choice_candidate_source_summary(archive)
    if multiple_choice_summary["ambiguous_locators"]:
        print(
            "  Multiple Choices leaves: "
            f"ambiguous_locators={multiple_choice_summary['ambiguous_locators']:,}, "
            f"ambiguous_groups={multiple_choice_summary['ambiguous_groups']:,}, "
            f"candidate_urls={multiple_choice_summary['candidate_source_urls']:,}, "
            f"candidate_sources_available="
            f"{multiple_choice_summary['candidate_sources_available']:,}, "
            f"groups_fully_available="
            f"{multiple_choice_summary['groups_fully_available']:,}, "
            f"groups_partially_available="
            f"{multiple_choice_summary['groups_partially_available']:,}, "
            f"groups_without_available_candidates="
            f"{multiple_choice_summary['groups_without_available_candidates']:,}, "
            f"groups_without_candidate_urls="
            f"{multiple_choice_summary['groups_without_candidate_urls']:,}"
        )
    affecting = _scan_affecting_acts(archive)
    missing = sum(
        1 for a in affecting
        if not archive.has(f"{_LEG_BASE}/{a}/enacted/data.xml")
        and not archive.has(_missing_enacted_locator(a))
    )
    print(f"  Affecting: {len(affecting):,} referenced, {missing:,} missing")


# ── CLI orchestration ────────────────────────────────────────────────────────


def _open_archive(db_path: Path, *, readonly: bool = False) -> Farchive:
    if readonly:
        if not db_path.exists():
            raise SystemExit(f"ERROR: archive not found: {db_path}")
    else:
        validate_farchive_create_path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
    return Farchive(
        db_path,
        compression=CompressionPolicy(
            auto_train_thresholds={"xml": 1000, "csv": 100},
            dict_target_sizes={"xml": 112 * 1024},
            compression_level=9,
        ),
        readonly=readonly,
    )


def _manifest(types: list[str], http: _HTTP) -> dict[str, list[UKActRow]]:
    print("\n[enumerate] CSV feeds (not stored)")
    return do_enumerate(types, http)


def run_acquire(archive: Farchive, http: _HTTP, *, types: list[str], enacted_only: bool) -> None:
    manifest = _manifest(types, http)
    print(f"\n[download] enacted={'only' if enacted_only else '+current+effects'}")
    r = do_download(manifest, archive, http, enacted_only=enacted_only)
    print(
        f"  enacted+{r['enacted']:,}  current+{r['current']:,}  "
        f"effects+{r['effects']:,}  multiple_choices={r['multiple_choices']:,}  "
        f"candidate_sources+{r['candidate_sources']:,}"
    )


def run_affecting(
    archive: Farchive, http: _HTTP, *, affecting_types: Optional[list[str]], events_jsonl: Optional[str]
) -> None:
    print("\n[affecting] missing enacted XML")
    diagnostics: list[dict[str, object]] = []
    r = do_affecting(
        archive, http,
        types=set(affecting_types) if affecting_types else None,
        diagnostics_out=diagnostics,
    )
    print(f"  fetched={r['fetched']:,}  failed={r['failed']:,}  404={r['gone']:,}")
    if events_jsonl:
        path = Path(events_jsonl)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in diagnostics),
            encoding="utf-8",
        )
        print(f"  acquisition_events={len(diagnostics):,}  events_jsonl={path}")


def run_refresh(
    archive: Farchive, http: _HTTP, *, statutes: list[str], force: bool
) -> None:
    print("\n[refresh] mutable resources")
    r = do_refresh(archive, http, statute_ids=set(statutes) if statutes else None, force=force)
    print(f"  current+{r['current']:,}  effects+{r['effects']:,}")


def run_repair_multiple_choices(
    archive: Farchive,
    http: _HTTP,
    *,
    statutes: list[str],
    limit: int,
) -> None:
    print("\n[repair-multiple-choices] cached ambiguity locators")
    r = do_repair_multiple_choices(
        archive,
        http,
        statute_ids=set(statutes) if statutes else None,
        limit=limit,
    )
    print(
        f"  ambiguous={r['ambiguous_locators']:,}  "
        f"groups={r['ambiguous_groups']:,}  "
        f"repaired={r['repaired_locators']:,}  "
        f"candidate_urls={r['candidate_source_urls']:,}  "
        f"candidate_sources_available={r['candidate_sources_available']:,}  "
        f"candidate_sources+{r['candidate_sources']:,}  "
        f"direct_sources+{r['direct_sources']:,}  "
        f"no_candidates={r['no_candidates']:,}  failed={r['failed']:,}"
    )


def run_pdf(
    archive: Farchive,
    *,
    limit: int,
    pdf_only: bool,
    delay: float,
    worklist_only: bool,
) -> None:
    """Resumable, bounded crawl of the PDF-only worklist (no network discovery).

    The worklist is a pure in-archive scan (enacted stubs that name their own
    PDF inline and whose PDF blob is not yet in the ``leg://pdf/`` lane), so the
    crawl skips already-acquired PDFs and resumes cleanly.  Output is bounded:
    aggregate counts plus (on failure) a capped tail of failing per-act reports.
    """
    from lawvm.uk_legislation.pdf_acquire import crawl_pdf_worklist, iter_pdf_worklist

    lane = "pdf-only" if pdf_only else "all-pdf-named"
    if worklist_only:
        pending = iter_pdf_worklist(archive, pdf_only=pdf_only)
        print(f"\n[pdf] {lane} worklist remaining: {len(pending):,}  (fetch nothing)")
        return

    print(f"\n[pdf] {lane} crawl  limit={limit or 'ALL'}  delay={delay}s")
    report = crawl_pdf_worklist(
        archive, limit=limit, pdf_only=pdf_only, delay=delay, verbose=True
    )
    print(
        f"  worklist={report.worklist_total:,}  attempted={report.attempted:,}  "
        f"acquired={report.acquired:,}  cached={report.already_cached:,}  "
        f"errors={report.errors:,}  bytes={report.total_bytes:,}  "
        f"remaining={report.remaining:,}"
    )
    if report.error_reports:
        # Bounded failure tail (never dump the whole corpus on stdout).
        by_error: dict[str, int] = {}
        for r in report.error_reports:
            by_error[r.error or "unknown"] = by_error.get(r.error or "unknown", 0) + 1
        print("  error breakdown: " + "  ".join(f"{k}={v}" for k, v in sorted(by_error.items())))
        for r in report.error_reports[:10]:
            print(f"    ! {r.statute_id}: {r.error}")


def main(args: Any) -> None:
    command = getattr(args, "uk_corpus_command", None) or "stats"
    db_path = Path(getattr(args, "db", _DEFAULT_ARCHIVE))
    archive = _open_archive(db_path, readonly=command == "stats")
    try:
        if command == "stats":
            do_stats(archive)
            return
        if command == "train-dict":
            print(f"Dictionary trained: dict_id={archive.train_dict(storage_class='xml')}")
            return
        if command == "repack":
            st = archive.repack(storage_class="xml")
            print(f"Repacked: {st.blobs_repacked:,} blobs, saved {st.bytes_saved:,} bytes")
            return

        if command == "pdf":
            # PDF lane uses its own courteous fetcher (pdf_acquire), not _HTTP.
            print(f"UK corpus → {db_path}")
            run_pdf(
                archive,
                limit=int(getattr(args, "limit", 0) or 0),
                pdf_only=not bool(getattr(args, "all_pdf_named", False)),
                delay=float(getattr(args, "delay", 1.0)),
                worklist_only=bool(getattr(args, "worklist_only", False)),
            )
            st = archive.stats()
            print(f"\nDone. {st.locator_count:,} locators, {st.total_stored_bytes / 1e6:.1f} MB stored")
            return

        http = _HTTP(delay=getattr(args, "delay", _DEFAULT_DELAY))
        print(f"UK corpus → {db_path}")
        if command in ("acquire", "all"):
            run_acquire(
                archive, http,
                types=getattr(args, "types", None) or PRIMARY_TYPES,
                enacted_only=bool(getattr(args, "enacted_only", False)),
            )
        if command in ("affecting", "all"):
            run_affecting(
                archive, http,
                affecting_types=getattr(args, "affecting_types", None),
                events_jsonl=getattr(args, "events_jsonl", None),
            )
        if command in ("refresh", "all"):
            run_refresh(
                archive, http,
                statutes=getattr(args, "statute", None) or [],
                force=bool(getattr(args, "force_refresh", False)),
            )
        if command in ("repair-multiple-choices", "all"):
            run_repair_multiple_choices(
                archive,
                http,
                statutes=getattr(args, "statute", None) or [],
                limit=int(getattr(args, "limit", 0) or 0),
            )
        if command not in ("acquire", "affecting", "refresh", "repair-multiple-choices", "all"):
            raise SystemExit(f"Unknown uk-corpus subcommand: {command}")
        st = archive.stats()
        print(f"\nDone. {st.locator_count:,} locators, {st.total_stored_bytes / 1e6:.1f} MB stored")
    finally:
        archive.close()


__all__ = [
    "PRIMARY_TYPES",
    "SECONDARY_TYPES",
    "do_affecting",
    "do_download",
    "do_enumerate",
    "run_pdf",
    "do_repair_multiple_choices",
    "do_refresh",
    "do_stats",
    "main",
]


if __name__ == "__main__":  # pragma: no cover — convenience for direct execution
    sys.exit("Run via: lawvm uk-corpus <subcommand>")
