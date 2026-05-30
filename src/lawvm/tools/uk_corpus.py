"""uk_corpus.py — native ``lawvm uk-corpus`` acquisition and curation.

Acquires the UK legislation corpus into a Farchive. Single, fully resumable,
idempotent pipeline: only fetches what is missing or stale. This is the native
CLI home for UK corpus sync (harmonized with ``ee-corpus`` and ``nz-corpus``),
reachable as ``lawvm uk-corpus <subcommand>``.

Subcommands (``lawvm uk-corpus <sub>``):
  acquire    enumerate primary acts via CSV and download enacted/current/effects
  affecting  fetch enacted XML for affecting acts discovered in effects feeds
  refresh    re-fetch mutable resources (current XML + effects feeds) if stale
  stats      archive summary
  train-dict / repack   compression maintenance
  all        acquire + affecting + refresh

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
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from farchive import CompressionPolicy, Farchive

from lawvm.core.http_identity import LAWVM_USER_AGENT

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


def _missing_enacted_locator(act_id: str) -> str:
    """Negative-cache locator for permanently missing affecting-act enacted XML."""
    return f"leg://missing/uk/{act_id}/enacted/data.xml"


def _affecting_acquisition_event(
    *, affecting_act_id: str, url: str, status: str, rule_id: str, reason: str, blocking: bool
) -> dict[str, object]:
    return {
        "rule_id": rule_id,
        "phase": "acquisition",
        "family": "source_pathology",
        "affecting_act_id": affecting_act_id,
        "locator": _missing_enacted_locator(affecting_act_id),
        "url": url,
        "status": status,
        "reason": reason,
        "blocking": blocking,
        "strict_disposition": "block" if blocking else "record",
        "quirks_disposition": "record",
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
            except Exception:  # noqa: BLE001 — transport robustness for a long batch crawl
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


def _parse_csv_acts(act_type: str, data: bytes) -> list[dict]:
    text = data.decode("utf-8-sig", errors="replace")
    acts = []
    for row in csv.DictReader(io.StringIO(text)):
        year = (row.get("YEAR") or row.get("Year") or "").strip()
        num = (row.get("NUMBER") or row.get("Number") or "").strip()
        title = (row.get("TITLE") or row.get("Title") or "").strip()
        if year and num:
            acts.append({"type": act_type, "year": year, "num": num, "title": title})
    return acts


def _enumerate_type(act_type: str, http: _HTTP) -> list[dict]:
    all_acts: list[dict] = []
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
            except Exception:  # noqa: BLE001
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
    except Exception:  # noqa: BLE001
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


def do_enumerate(types: list[str], http: _HTTP) -> dict[str, list[dict]]:
    manifest: dict[str, list[dict]] = {}
    total = 0
    for t in types:
        acts = _enumerate_type(t, http)
        manifest[t] = acts
        total += len(acts)
        print(f"  {t}: {len(acts):,} acts")
    print(f"  Total: {total:,} acts")
    return manifest


def do_download(
    manifest: dict[str, list[dict]], archive: Farchive, http: _HTTP, *, enacted_only: bool = False
) -> dict:
    all_acts = [(t, a) for t, acts in manifest.items() for a in acts]
    total = len(all_acts)
    n_enacted = n_current = n_effects = 0
    for i, (_, act) in enumerate(all_acts, 1):
        t, y, n = act["type"], act["year"], act["num"]
        base = f"{_LEG_BASE}/{t}/{y}/{n}"
        enacted_url = f"{base}/enacted/data.xml"
        if not archive.has(enacted_url):
            data = http.get(enacted_url)
            if data and len(data) > 50 and _store_if_new(archive, enacted_url, data, "xml"):
                n_enacted += 1
        if not enacted_only:
            current_url = f"{base}/data.xml"
            if _is_stale(archive, current_url, _TTL_CURRENT):
                data = http.get(current_url)
                if data and len(data) > 50:
                    _store_if_new(archive, current_url, data, "xml")
                    n_current += 1
            if _fetch_effects_pages(t, y, n, archive, http) > 0:
                n_effects += 1
        if i % 500 == 0 or i == total:
            st = archive.stats()
            print(
                f"  [{i:,}/{total:,}]  enacted+{n_enacted:,}  current+{n_current:,}  "
                f"effects+{n_effects:,}  archive={st.locator_count:,} locators  last={t}/{y}/{n}"
            )
    return {"enacted": n_enacted, "current": n_current, "effects": n_effects}


def do_affecting(
    archive: Farchive, http: _HTTP, *, types: Optional[set[str]] = None,
    diagnostics_out: Optional[list[dict[str, object]]] = None,
) -> dict:
    affecting = _scan_affecting_acts(archive)
    if types:
        affecting = {a for a in affecting if a.split("/")[0] in types}
    to_fetch = [
        a for a in sorted(affecting)
        if (not archive.has(f"{_LEG_BASE}/{a}/enacted/data.xml"))
        and (not archive.has(_missing_enacted_locator(a)))
    ]
    print(f"  {len(affecting) - len(to_fetch):,} cached, {len(to_fetch):,} to fetch")
    n_ok = n_fail = n_404 = 0
    for i, aid in enumerate(to_fetch, 1):
        url = f"{_LEG_BASE}/{aid}/enacted/data.xml"
        data, status = http.get_with_status(url)
        if data and len(data) > 50 and _is_storable_xml(data):
            archive.store(url, data, storage_class="xml")
            n_ok += 1
        elif status in {404, 410}:
            archive.store(_missing_enacted_locator(aid), b"404", storage_class="text")
            n_404 += 1
            if diagnostics_out is not None:
                diagnostics_out.append(_affecting_acquisition_event(
                    affecting_act_id=aid, url=url, status="permanent_missing_cached",
                    rule_id="uk_acquire_affecting_enacted_permanent_missing",
                    reason=f"http_{status}", blocking=False))
        else:
            n_fail += 1
            if diagnostics_out is not None:
                diagnostics_out.append(_affecting_acquisition_event(
                    affecting_act_id=aid, url=url, status="error",
                    rule_id="uk_acquire_affecting_enacted_fetch_failed",
                    reason=f"http_{status}" if status is not None else "transport_error",
                    blocking=True))
        if i % 1000 == 0 or i == len(to_fetch):
            print(f"  [{i:,}/{len(to_fetch):,}]  ok={n_ok:,}  fail={n_fail:,}  404={n_404:,}  last={aid}")
    return {"fetched": n_ok, "failed": n_fail, "gone": n_404}


def do_refresh(
    archive: Farchive, http: _HTTP, *, statute_ids: Optional[set[str]] = None, force: bool = False
) -> dict:
    n_current = n_effects = 0
    if statute_ids:
        for sid in sorted(statute_ids):
            act_type, year, number = _split_statute_id(sid)
            current_url = f"{_LEG_BASE}/{act_type}/{year}/{number}/data.xml"
            if force or _is_stale(archive, current_url, _TTL_CURRENT):
                data = http.get(current_url)
                if data and len(data) > 50 and _store_if_new(archive, current_url, data, "xml"):
                    n_current += 1
            if _fetch_effects_pages(act_type, year, number, archive, http, force=force) > 0:
                n_effects += 1
        return {"current": n_current, "effects": n_effects}
    for loc in archive.locators("%/data.xml"):
        if "/enacted/" in loc:
            continue
        if _is_stale(archive, loc, _TTL_CURRENT):
            data = http.get(loc)
            if data and len(data) > 50:
                _store_if_new(archive, loc, data, "xml")
                n_current += 1
    for loc in archive.locators("%/data.feed%"):
        if _is_stale(archive, loc, _TTL_EFFECTS):
            data = http.get(loc)
            if data:
                _store_if_new(archive, loc, data, "xml")
                n_effects += 1
    return {"current": n_current, "effects": n_effects}


def do_stats(archive: Farchive) -> None:
    st = archive.stats()
    print(f"\n{'=' * 60}\nArchive: {st.db_path}\n{'=' * 60}")
    print(f"  Locators:     {st.locator_count:,}")
    print(f"  Blobs:        {st.blob_count:,}")
    print(f"  Raw:          {st.total_raw_bytes / 1e6:.1f} MB")
    print(f"  Stored:       {st.total_stored_bytes / 1e6:.1f} MB")
    print(f"  Compression:  {(st.compression_ratio or 0.0):.1f}x")
    cats: Counter = Counter()
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
    affecting = _scan_affecting_acts(archive)
    missing = sum(
        1 for a in affecting
        if not archive.has(f"{_LEG_BASE}/{a}/enacted/data.xml")
        and not archive.has(_missing_enacted_locator(a))
    )
    print(f"  Affecting: {len(affecting):,} referenced, {missing:,} missing")


# ── CLI orchestration ────────────────────────────────────────────────────────


def _open_archive(db_path: Path) -> Farchive:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return Farchive(
        db_path,
        compression=CompressionPolicy(
            auto_train_thresholds={"xml": 1000, "csv": 100},
            dict_target_sizes={"xml": 112 * 1024},
            compression_level=9,
        ),
    )


def _manifest(types: list[str], http: _HTTP) -> dict[str, list[dict]]:
    print("\n[enumerate] CSV feeds (not stored)")
    return do_enumerate(types, http)


def run_acquire(archive: Farchive, http: _HTTP, *, types: list[str], enacted_only: bool) -> None:
    manifest = _manifest(types, http)
    print(f"\n[download] enacted={'only' if enacted_only else '+current+effects'}")
    r = do_download(manifest, archive, http, enacted_only=enacted_only)
    print(f"  enacted+{r['enacted']:,}  current+{r['current']:,}  effects+{r['effects']:,}")


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


def main(args: Any) -> None:
    command = getattr(args, "uk_corpus_command", None) or "stats"
    db_path = Path(getattr(args, "db", _DEFAULT_ARCHIVE))
    archive = _open_archive(db_path)
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
        if command not in ("acquire", "affecting", "refresh", "all"):
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
    "do_refresh",
    "do_stats",
    "main",
]


if __name__ == "__main__":  # pragma: no cover — convenience for direct execution
    sys.exit("Run via: lawvm uk-corpus <subcommand>")
