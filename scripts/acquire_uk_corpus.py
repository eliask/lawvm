#!/usr/bin/env python3
"""acquire_uk_corpus.py — Acquire full UK legislation corpus into Farchive.

Single-script, fully resumable pipeline.  Idempotent: safe to run repeatedly.
Only fetches what is missing or stale.

Usage (from LawVM/ dir):
    # Full acquisition (all phases, skips what's already cached)
    uv run python scripts/acquire_uk_corpus.py

    # Just enumerate + download primary acts (no effects, no secondary)
    uv run python scripts/acquire_uk_corpus.py -- enacted-only

    # Refresh mutable resources only (current XML + effects feeds)
    uv run python scripts/acquire_uk_corpus.py -- refresh

    # Fetch missing affecting acts discovered in effects feeds
    uv run python scripts/acquire_uk_corpus.py -- affecting

    # Stats
    uv run python scripts/acquire_uk_corpus.py -- stats

    # Maintenance
    uv run python scripts/acquire_uk_corpus.py -- train-dict
    uv run python scripts/acquire_uk_corpus.py -- repack

Immutability model:
  ──────────────────────────────────────────────────────────────────────
  Resource              Mutability    Strategy
  ──────────────────────────────────────────────────────────────────────
  CSV enumeration       EPHEMERAL     Fetch on demand, never store.
                                      The API is efficient enough.
  Enacted XML           IMMUTABLE     Store once.  Skip if digest present.
  Affecting act XML     IMMUTABLE     Store once.  Skip if digest present.
  Current XML           SLOW (TTL)    Re-fetch if last_confirmed > TTL.
  Effects feeds         SLOW (TTL)    Re-fetch if last_confirmed > TTL.
  ──────────────────────────────────────────────────────────────────────

Rate limiting:
  - Default 0.3 s between requests (~200 req/min)
  - HTTP 429 → exponential backoff, respects Retry-After
  - HTTP 404/410 → recorded as permanent miss, never retried
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from farchive import Farchive, CompressionPolicy

# ── Constants ────────────────────────────────────────────────────────────

_LEG_BASE = "https://www.legislation.gov.uk"
_USER_AGENT = "LawVM/1.0 (+https://github.com/lawvm)"

# Primary act types (enumerated via CSV feeds)
PRIMARY_TYPES = ["ukpga", "asp", "asc", "nia", "eur"]

# Secondary types that appear as affecting acts in effects feeds
SECONDARY_TYPES = frozenset(
    [
        "ukpga",
        "uksi",
        "asp",
        "asc",
        "nia",
        "nisi",
        "ssi",
        "wsi",
        "mnia",
        "apni",
        "ukci",
        "eur",
        "ukla",
        "anaw",
        "nisr",
        "mwa",
        "eudn",
    ]
)

_DEFAULT_DELAY = 0.3  # seconds between requests
_CSV_PAGE_SIZE = 500
_MAX_RETRIES = 3
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# TTLs (seconds) — only matters for mutable resources
_TTL_CURRENT = 300 * 86400  # 30 days
_TTL_EFFECTS = 300 * 86400  # 7 days

_DEFAULT_ARCHIVE = Path(__file__).parent.parent / "data" / "uk_legislation.farchive"


def _missing_enacted_locator(act_id: str) -> str:
    """Negative-cache locator for permanently missing enacted XML in UK effects.

    This keeps the acquisition pipeline from repeatedly trying to fetch known
    permanent misses. The locator namespace is local to this project and only used
    as a durable marker.
    """
    return f"leg://missing/uk/{act_id}/enacted/data.xml"


def _affecting_acquisition_event(
    *,
    affecting_act_id: str,
    url: str,
    status: str,
    rule_id: str,
    reason: str,
    blocking: bool,
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


# ── HTTP client ──────────────────────────────────────────────────────────


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
        """Fetch URL.

        Returns a tuple ``(data, status_code)``.

        - ``data`` is bytes on success, ``None`` on failure.
        - ``status_code`` is HTTP status code on hard failures, ``None`` for
          transport/parse failures after retries.
        """
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
                    data = resp.read()
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
            except Exception:
                if attempt <= _MAX_RETRIES:
                    time.sleep(min(2**attempt * 0.5, 15))
                    continue
                return None, None
        return None, None

    def get(self, url: str) -> Optional[bytes]:
        """Fetch URL.  Returns bytes on success, None on 404/410/exhausted."""
        data, _ = self.get_with_status(url)
        return data


# ── Helpers ──────────────────────────────────────────────────────────────


def _is_stale(archive: Farchive, url: str, ttl: float) -> bool:
    """Return True if the locator is missing or last_confirmed_at > ttl ago."""
    spans = archive.history(url)
    if not spans:
        return True  # missing → need to fetch
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
    """Fetch paginated CSV, return list of acts.  Does NOT store CSV."""
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
    """Store only if the locator doesn't already have this digest."""
    spans = archive.history(url)
    if spans:
        import hashlib

        digest = hashlib.sha256(data).hexdigest()
        if spans[-1].digest == digest:
            return False  # identical content, skip
    archive.store(url, data, storage_class=sc)
    return True


def _fetch_effects_pages(
    act_type: str,
    year: str,
    number: str,
    archive: Farchive,
    http: _HTTP,
) -> int:
    """Fetch all pages of an effects feed.  Returns pages fetched (0 if none)."""
    base = f"{_LEG_BASE}/changes/affected/{act_type}/{year}/{number}/data.feed"
    p1_url = f"{base}?results-count=50&sort=modified"

    if not _is_stale(archive, p1_url, _TTL_EFFECTS):
        # Already fresh — read total pages from cache
        data = archive.get(p1_url)
        if data:
            try:
                root = ET.fromstring(data)
                el = root.find(".//{http://www.legislation.gov.uk/namespaces/legislation}totalPages")
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
        el = root.find(".//{http://www.legislation.gov.uk/namespaces/legislation}totalPages")
        total_pages = int(el.text) if el is not None and el.text else 1
    except Exception:
        pass

    for p in range(2, total_pages + 1):
        purl = f"{p1_url}&page={p}"
        if not _is_stale(archive, purl, _TTL_EFFECTS):
            continue
        pdata = http.get(purl)
        if pdata:
            _store_if_new(archive, purl, pdata, "xml")

    return total_pages


def _scan_affecting_acts(archive: Farchive) -> set[str]:
    """Scan all effects feeds in archive, return unique affecting act IDs."""
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


# ── Phases ───────────────────────────────────────────────────────────────


def do_enumerate(types: list[str], http: _HTTP) -> dict[str, list[dict]]:
    """Phase 1: enumerate acts via CSV (never stored)."""
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
    manifest: dict[str, list[dict]],
    archive: Farchive,
    http: _HTTP,
    *,
    enacted_only: bool = False,
) -> dict:
    """Phase 2: download enacted XML, current XML, effects feeds."""
    all_acts = [(t, a) for t, acts in manifest.items() for a in acts]
    total = len(all_acts)
    n_enacted = n_current = n_effects = 0

    for i, (_, act) in enumerate(all_acts, 1):
        t, y, n = act["type"], act["year"], act["num"]
        base = f"{_LEG_BASE}/{t}/{y}/{n}"

        # Enacted XML — IMMUTABLE
        enacted_url = f"{base}/enacted/data.xml"
        if not archive.has(enacted_url):
            data = http.get(enacted_url)
            if data and len(data) > 50:
                archive.store(enacted_url, data, storage_class="xml")
                n_enacted += 1

        if not enacted_only:
            # Current XML — SLOW, respect TTL
            current_url = f"{base}/data.xml"
            if _is_stale(archive, current_url, _TTL_CURRENT):
                data = http.get(current_url)
                if data and len(data) > 50:
                    _store_if_new(archive, current_url, data, "xml")
                    n_current += 1

            # Effects feeds — SLOW, respect TTL
            pages = _fetch_effects_pages(t, y, n, archive, http)
            if pages > 0:
                n_effects += 1

        if i % 500 == 0 or i == total:
            st = archive.stats()
            print(
                f"  [{i:,}/{total:,}]  enacted+{n_enacted:,}  "
                f"current+{n_current:,}  effects+{n_effects:,}  "
                f"archive={st.locator_count:,} locators  "
                f"last={t}/{y}/{n}"
            )

    return {"enacted": n_enacted, "current": n_current, "effects": n_effects}


def do_affecting(
    archive: Farchive,
    http: _HTTP,
    *,
    types: Optional[set[str]] = None,
    diagnostics_out: Optional[list[dict[str, object]]] = None,
) -> dict:
    """Phase 3: fetch enacted XML for affecting acts discovered in effects feeds."""
    affecting = _scan_affecting_acts(archive)
    if types:
        affecting = {a for a in affecting if a.split("/")[0] in types}

    to_fetch = [
        a
        for a in sorted(affecting)
        if (not archive.has(f"{_LEG_BASE}/{a}/enacted/data.xml"))
        and (not archive.has(_missing_enacted_locator(a)))
    ]
    already = len(affecting) - len(to_fetch)
    print(f"  {already:,} cached, {len(to_fetch):,} to fetch")

    n_ok = n_fail = n_404 = 0
    for i, aid in enumerate(to_fetch, 1):
        url = f"{_LEG_BASE}/{aid}/enacted/data.xml"
        data, status = http.get_with_status(url)
        if data and len(data) > 50:
            archive.store(url, data, storage_class="xml")
            n_ok += 1
        elif status in {404, 410}:
            archive.store(_missing_enacted_locator(aid), b"404", storage_class="text")
            n_404 += 1
            if diagnostics_out is not None:
                diagnostics_out.append(
                    _affecting_acquisition_event(
                        affecting_act_id=aid,
                        url=url,
                        status="permanent_missing_cached",
                        rule_id="uk_acquire_affecting_enacted_permanent_missing",
                        reason=f"http_{status}",
                        blocking=False,
                    )
                )
        else:
            n_fail += 1
            if diagnostics_out is not None:
                diagnostics_out.append(
                    _affecting_acquisition_event(
                        affecting_act_id=aid,
                        url=url,
                        status="error",
                        rule_id="uk_acquire_affecting_enacted_fetch_failed",
                        reason=f"http_{status}" if status is not None else "transport_error",
                        blocking=True,
                    )
                )

        if i % 1000 == 0 or i == len(to_fetch):
            print(f"  [{i:,}/{len(to_fetch):,}]  ok={n_ok:,}  fail={n_fail:,}  404={n_404:,}  last={aid}")

    return {"fetched": n_ok, "failed": n_fail, "gone": n_404}


def do_refresh(archive: Farchive, http: _HTTP) -> dict:
    """Re-fetch mutable resources (current XML + effects feeds) if stale."""
    n_current = n_effects = 0

    # Current XMLs
    for loc in archive.locators("%/data.xml"):
        if "/enacted/" in loc:
            continue
        if _is_stale(archive, loc, _TTL_CURRENT):
            data = http.get(loc)
            if data and len(data) > 50:
                _store_if_new(archive, loc, data, "xml")
                n_current += 1

    # Effects feeds
    for loc in archive.locators("%/data.feed%"):
        if _is_stale(archive, loc, _TTL_EFFECTS):
            data = http.get(loc)
            if data:
                _store_if_new(archive, loc, data, "xml")
                n_effects += 1

    return {"current": n_current, "effects": n_effects}


def do_stats(archive: Farchive) -> None:
    st = archive.stats()
    print(f"\n{'=' * 60}")
    print(f"Archive: {st.db_path}")
    print(f"{'=' * 60}")
    print(f"  Locators:     {st.locator_count:,}")
    print(f"  Blobs:        {st.blob_count:,}")
    print(f"  Raw:          {st.total_raw_bytes / 1e6:.1f} MB")
    print(f"  Stored:       {st.total_stored_bytes / 1e6:.1f} MB")
    print(f"  Compression:  {st.compression_ratio:.1f}x")

    cats = Counter()
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
        1
        for a in affecting
        if (
            not archive.has(f"{_LEG_BASE}/{a}/enacted/data.xml")
            and not archive.has(_missing_enacted_locator(a))
        )
    )
    print(f"  Affecting: {len(affecting):,} referenced, {missing:,} missing")


# ── CLI ──────────────────────────────────────────────────────────────────


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Acquire UK legislation corpus into Farchive",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "command",
        nargs="?",
        default="all",
        choices=["all", "enumerate", "download", "affecting", "refresh", "stats", "train-dict", "repack"],
        help="What to do (default: all)",
    )
    ap.add_argument("--types", nargs="+", default=PRIMARY_TYPES)
    ap.add_argument("--delay", type=float, default=_DEFAULT_DELAY)
    ap.add_argument("--archive", type=Path, default=_DEFAULT_ARCHIVE)
    ap.add_argument("--enacted-only", action="store_true")
    ap.add_argument("--affecting-types", nargs="+", default=None)
    ap.add_argument(
        "--events-jsonl",
        metavar="PATH",
        help="write structured acquisition event rows for affecting-act permanent misses/failures",
    )
    args = ap.parse_args()

    args.archive.parent.mkdir(parents=True, exist_ok=True)
    archive = Farchive(
        args.archive,
        compression=CompressionPolicy(
            auto_train_thresholds={"xml": 1000, "csv": 100},
            dict_target_sizes={"xml": 112 * 1024},
            compression_level=9,
        ),
    )
    http = _HTTP(delay=args.delay)
    events_path = Path(args.events_jsonl) if args.events_jsonl else None

    cmd = args.command

    if cmd == "stats":
        do_stats(archive)
        archive.close()
        return

    if cmd == "train-dict":
        did = archive.train_dict(storage_class="xml")
        print(f"Dictionary trained: dict_id={did}")
        archive.close()
        return

    if cmd == "repack":
        st = archive.repack(storage_class="xml")
        print(f"Repacked: {st.blobs_repacked:,} blobs, saved {st.bytes_saved:,} bytes")
        archive.close()
        return

    # ── Main pipeline ──
    print(f"UK corpus → {args.archive}")
    affecting_diagnostics: list[dict[str, object]] = []

    # Always enumerate first (cheap, never stored)
    if cmd in ("all", "enumerate", "download"):
        print("\n[enumerate] CSV feeds (not stored)")
        manifest = do_enumerate(args.types, http)
    else:
        # Rebuild from cached CSVs if we have them, otherwise error
        manifest: dict[str, list[dict]] = {}
        for t in args.types:
            acts = []
            page = 1
            while True:
                url = f"{_LEG_BASE}/{t}/data.csv?results-count={_CSV_PAGE_SIZE}&page={page}"
                data = http.get(url)
                if not data:
                    break
                acts.extend(_parse_csv_acts(t, data))
                if len(acts) < _CSV_PAGE_SIZE:
                    break
                page += 1
            manifest[t] = acts
        total = sum(len(v) for v in manifest.values())
        print(f"[enumerate] {total:,} acts from cached CSVs")

    if cmd in ("all", "download"):
        print(f"\n[download] enacted={'only' if args.enacted_only else '+current+effects'}")
        r = do_download(manifest, archive, http, enacted_only=args.enacted_only)
        print(f"  enacted+{r['enacted']:,}  current+{r['current']:,}  effects+{r['effects']:,}")

    if cmd in ("all", "affecting"):
        print("\n[affecting] missing enacted XML")
        r = do_affecting(
            archive,
            http,
            types=set(args.affecting_types) if args.affecting_types else None,
            diagnostics_out=affecting_diagnostics,
        )
        print(f"  fetched={r['fetched']:,}  failed={r['failed']:,}  404={r['gone']:,}")
        if events_path:
            events_path.parent.mkdir(parents=True, exist_ok=True)
            events_path.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in affecting_diagnostics),
                encoding="utf-8",
            )
            print(f"  acquisition_events={len(affecting_diagnostics):,}  events_jsonl={events_path}")

    if cmd == "refresh":
        print("\n[refresh] mutable resources")
        r = do_refresh(archive, http)
        print(f"  current+{r['current']:,}  effects+{r['effects']:,}")

    st = archive.stats()
    print(f"\nDone. {st.locator_count:,} locators, {st.total_stored_bytes / 1e6:.1f} MB stored")
    archive.close()


if __name__ == "__main__":
    main()
