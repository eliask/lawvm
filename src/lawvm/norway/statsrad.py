"""Norway `Offisielt fra statsråd` acquisition helpers.

This lane is an evidence sidecar, not the replay substrate.

Artifacts are stored in the Norway Farchive under both:

- the real regjeringen.no URL
- canonical `no://statsrad/...` locators
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Iterable, Protocol, cast
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from lxml import etree, html

from lawvm.norway.sources import open_no_archive, resolve_no_source_path

STATSRAD_SOURCE_NAME = "regjeringen.no/offisielt-fra-statsrad"

_BASE_INDEX_URL = (
    "https://www.regjeringen.no/no/aktuelt/offisielt-fra-statsrad/"
    "offisielt-fra-statsrad1/id30297/"
)
_MAX_INDEX_PAGES = 2000
_INDEX_URL_RE = re.compile(r"^https://www\.regjeringen\.no/.+/id30297/\?page=(\d+)$")
_ARTICLE_ID_RE = re.compile(r"/(id\d{5,})/?(?:\?.*)?$")
_WS_RE = re.compile(r"\s+")
_DATE_RE = re.compile(r"\b(\d{1,2})\.\s*([A-Za-zÆØÅæøå]+)\s+(\d{4})\b")
_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_LOVVEDTAK_RE = re.compile(r"\bLovvedtak\s+\d+\s+\(\d{4}-\d{4}\)", re.IGNORECASE)
_LOV_NR_RE = re.compile(r"\bLov\s+nr\.\s*\d+\b", re.IGNORECASE)
_COMMENCE_SENTENCE_RE = re.compile(
    r"([^.]{0,120}?\b(?:loven|loven §|lovvedtak|endringsloven)[^.]*?\b"
    r"(?:trer i kraft|settes i kraft|delt ikrafttredelse)[^.]*\.)",
    re.IGNORECASE,
)
_SANCTION_SENTENCE_RE = re.compile(
    r"([^.]{0,120}?\bSanksjon av Stortingets vedtak[^.]*\.)",
    re.IGNORECASE,
)

_MONTHS = {
    "januar": "01",
    "februar": "02",
    "mars": "03",
    "april": "04",
    "mai": "05",
    "juni": "06",
    "juli": "07",
    "august": "08",
    "september": "09",
    "oktober": "10",
    "november": "11",
    "desember": "12",
}


class _ArchiveLike(Protocol):
    def store(
        self,
        locator: str,
        data: bytes,
        *,
        storage_class: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        ...

    def get(self, locator: str) -> bytes | None:
        ...

    def has(self, locator: str, *, max_age_hours: float = ...) -> bool:
        ...

    def fetch(self, locator: str, max_age_hours: float | None = None, content_type: str | None = None) -> bytes | None:
        ...

    def locators(self, pattern: str) -> list[str]:
        ...


@dataclass(frozen=True)
class NOStatsradArticle:
    bulletin_id: str
    url: str
    title: str
    published_date: str | None = None
    meeting_date: str | None = None


def _json_bytes(data: object) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


def _normalize_space(text: str) -> str:
    text = text.replace("\xa0", " ")
    if any(marker in text for marker in ("Ã", "Â", "â")):
        try:
            repaired = text.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            repaired = text
        else:
            text = repaired
    return _WS_RE.sub(" ", text).strip()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def no_statsrad_index_url(page: int) -> str:
    return f"{_BASE_INDEX_URL}?page={page}"


def no_statsrad_index_locator(page: int) -> str:
    return f"no://statsrad/index/page/{page}.html"


def no_statsrad_manifest_locator() -> str:
    return "no://statsrad/index/manifest.json"


def no_statsrad_article_raw_locator(bulletin_id: str) -> str:
    return f"no://statsrad/article/{bulletin_id}/raw.html"


def no_statsrad_article_record_locator(bulletin_id: str) -> str:
    return f"no://statsrad/article/{bulletin_id}/record.json"


def no_statsrad_article_events_locator(bulletin_id: str) -> str:
    return f"no://statsrad/article/{bulletin_id}/events.json"


def no_statsrad_article_id_from_url(url: str) -> str | None:
    match = _ARTICLE_ID_RE.search(url.strip())
    if not match:
        return None
    return match.group(1)


def _parse_norwegian_date(text: str) -> str | None:
    match = _ISO_RE.search(text)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    match = _DATE_RE.search(text)
    if not match:
        return None
    day = int(match.group(1))
    month = _MONTHS.get(match.group(2).lower())
    year = match.group(3)
    if not month:
        return None
    return f"{year}-{month}-{day:02d}"


def _local_listing_container(anchor: Any) -> Any:
    for container in anchor.iterancestors():
        tag = str(getattr(container, "tag", "")).lower()
        if tag in {"article", "li"}:
            return container
    parent = getattr(anchor, "getparent", lambda: None)()
    return parent if parent is not None else anchor


def _local_time_date(container: Any) -> str | None:
    try:
        time_nodes = container.xpath(".//time[1]")
    except Exception:
        time_nodes = []
    for node in time_nodes:
        datetime_value = _normalize_space(str(node.get("datetime") or ""))
        parsed = _parse_norwegian_date(datetime_value)
        if parsed:
            return parsed
        parsed = _parse_norwegian_date(_normalize_space("".join(node.itertext())))
        if parsed:
            return parsed
    return None


def _excerpt(text: str, offset: int, needle_len: int, context: int = 140) -> str:
    start = max(0, offset - context)
    end = min(len(text), offset + needle_len + context)
    body = " ".join(text[start:end].replace("\r", " ").replace("\n", " ").split())
    prefix = "... " if start > 0 else ""
    suffix = " ..." if end < len(text) else ""
    return f"{prefix}{body}{suffix}".strip()


def _find_literal(text: str, needle: str) -> dict[str, object] | None:
    if not needle:
        return None
    idx = text.lower().find(needle.lower())
    if idx < 0:
        return None
    return {
        "needle": needle,
        "offset": idx,
        "excerpt": _excerpt(text, idx, len(needle)),
    }


def _anchor_record(anchor: Any, href: str) -> NOStatsradArticle | None:
    title = _normalize_space("".join(anchor.itertext()))
    lowered_title = title.lower()
    if not title or ("offisielt fra statsråd" not in lowered_title and "offisielt fra statsrad" not in lowered_title):
        return None
    url = urljoin(_BASE_INDEX_URL, href)
    bulletin_id = no_statsrad_article_id_from_url(url)
    if not bulletin_id:
        return None
    container = _local_listing_container(anchor)
    meeting_date = _parse_norwegian_date(title)
    published_date = _local_time_date(container) or meeting_date
    return NOStatsradArticle(
        bulletin_id=bulletin_id,
        url=url,
        title=title,
        published_date=published_date,
        meeting_date=meeting_date,
    )


def parse_no_statsrad_listing(html_bytes: bytes) -> list[NOStatsradArticle]:
    root = html.fromstring(html_bytes)
    records: list[NOStatsradArticle] = []
    seen: set[str] = set()
    for anchor in cast(list[etree._Element], root.xpath("//a[@href]")):
        href = str(anchor.get("href") or "")
        if "offisielt-fra-statsrad" not in href:
            continue
        record = _anchor_record(anchor, href)
        if record is None or record.bulletin_id in seen:
            continue
        seen.add(record.bulletin_id)
        records.append(record)
    records.sort(key=lambda item: (item.published_date or "", item.bulletin_id), reverse=True)
    return records


def _archive_fetch(
    archive: _ArchiveLike,
    url: str,
    *,
    storage_class: str,
    max_age_hours: float | None = None,
) -> bytes | None:
    cached = archive.get(url)
    if cached is not None and max_age_hours is None:
        return cached
    if cached is not None and max_age_hours is not None and archive.has(url, max_age_hours=max_age_hours):
        return cached
    fetch_method = getattr(archive, "fetch", None)
    if callable(fetch_method):
        try:
            data = fetch_method(url, max_age_hours=max_age_hours, content_type=storage_class)
        except TypeError:
            data = fetch_method(url)
        if data:
            archive.store(url, data, storage_class=storage_class)
        return data
    try:
        import urllib.request

        req = urllib.request.Request(url, headers={"User-Agent": "LawVM-NO/1.0 (+https://github.com/lawvm)"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
    except Exception:
        return None
    if data:
        archive.store(url, data, storage_class=storage_class)
    return data


def fetch_no_statsrad_index_page(
    archive: _ArchiveLike,
    *,
    page: int,
    max_age_hours: float = 24.0,
    skip_existing: bool = False,
) -> dict[str, Any]:
    url = no_statsrad_index_url(page)
    locator = no_statsrad_index_locator(page)
    html_bytes = archive.get(locator) if skip_existing else None
    if html_bytes is None:
        html_bytes = _archive_fetch(archive, url, storage_class="html", max_age_hours=max_age_hours)
    if html_bytes is None:
        raise RuntimeError(f"failed to fetch statsrad index page {page}: {url}")
    records = parse_no_statsrad_listing(html_bytes)
    archive.store(
        locator,
        html_bytes,
        storage_class="html",
        metadata={"kind": "statsrad_index", "page": page, "url": url},
    )
    return {
        "page": page,
        "url": url,
        "article_count": len(records),
        "articles": [
            {
                "bulletin_id": record.bulletin_id,
                "url": record.url,
                "title": record.title,
                "published_date": record.published_date,
            }
            for record in records
        ],
    }


def build_no_statsrad_index(
    archive: _ArchiveLike,
    *,
    start_page: int = 1,
    max_pages: int | None = None,
    article_limit: int | None = None,
    max_age_hours: float = 24.0,
    skip_existing: bool = False,
) -> dict[str, Any]:
    pages: list[dict[str, Any]] = []
    articles: dict[str, dict[str, Any]] = {}
    stopped_reason = "no_list_items"
    page = start_page
    pages_remaining = _MAX_INDEX_PAGES if max_pages is None else max_pages
    while True:
        if pages_remaining <= 0:
            stopped_reason = "safety_cap" if max_pages is None else "max_pages"
            break
        page_report = fetch_no_statsrad_index_page(
            archive,
            page=page,
            max_age_hours=max_age_hours,
            skip_existing=skip_existing,
        )
        pages.append({"page": page_report["page"], "url": page_report["url"], "article_count": page_report["article_count"]})
        page_articles = page_report["articles"]
        if not page_articles:
            stopped_reason = "no_list_items"
            break
        for article in page_report["articles"]:
            articles.setdefault(article["bulletin_id"], article)
        if article_limit is not None and len(articles) >= article_limit:
            stopped_reason = "article_limit"
            break
        page += 1
        pages_remaining -= 1
    manifest = {
        "source_name": STATSRAD_SOURCE_NAME,
        "fetch_timestamp": _now_iso(),
        "discovered_page_count": len(pages),
        "discovered_article_count": len(articles),
        "stopped_reason": stopped_reason,
        "fetched_at": _now_iso(),
        "start_page": start_page,
        "page_count": len(pages),
        "article_count": len(articles),
        "pages": pages,
        "articles": list(articles.values()),
    }
    archive.store(no_statsrad_manifest_locator(), _json_bytes(manifest), storage_class="json")
    return manifest


def fetch_no_statsrad_articles(
    archive: _ArchiveLike,
    *,
    manifest: dict[str, Any],
    bulletin_ids: Iterable[str] | None = None,
    max_articles: int | None = None,
    max_age_hours: float = float("inf"),
    skip_existing: bool = False,
) -> dict[str, Any]:
    stored = 0
    selected = set(bulletin_ids or [])
    articles = list(manifest.get("articles", []))
    if selected:
        articles = [article for article in articles if str(article.get("bulletin_id")) in selected]
    if max_articles is not None:
        articles = articles[:max_articles]
    for article in articles:
        url = str(article["url"])
        bulletin_id = str(article["bulletin_id"])
        raw_locator = no_statsrad_article_raw_locator(bulletin_id)
        record_locator = no_statsrad_article_record_locator(bulletin_id)
        if skip_existing and archive.get(raw_locator) is not None and archive.get(record_locator) is not None:
            stored += 1
            continue
        html_bytes = _archive_fetch(archive, url, storage_class="html", max_age_hours=max_age_hours)
        if html_bytes is None:
            continue
        archive.store(
            raw_locator,
            html_bytes,
            storage_class="html",
            metadata={"kind": "statsrad_article_raw", "bulletin_id": bulletin_id, "url": url},
        )
        record = {
            "bulletin_id": bulletin_id,
            "url": url,
            "title": article.get("title", ""),
            "published_date": article.get("published_date"),
            "source_name": STATSRAD_SOURCE_NAME,
            "fetch_timestamp": _now_iso(),
        }
        archive.store(
            record_locator,
            _json_bytes(record),
            storage_class="json",
            metadata={"kind": "statsrad_article_record", "bulletin_id": bulletin_id, "url": url},
        )
        stored += 1
    return {"stored_articles": stored, "requested_articles": len(articles[:max_articles])}


def _extract_dates(text: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for iso in _ISO_RE.findall(text):
        value = f"{iso[0]}-{iso[1]}-{iso[2]}"
        if value not in seen:
            seen.add(value)
            out.append(value)
    for match in _DATE_RE.finditer(text):
        value = _parse_norwegian_date(match.group(0))
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def extract_no_statsrad_events(html_bytes: bytes, *, bulletin_id: str, bulletin_date: str | None, title: str = "") -> list[dict[str, Any]]:
    return extract_statsrad_events_from_article(
        html_bytes,
        bulletin_id=bulletin_id,
        bulletin_url="",
        fallback_title=title,
        fallback_published_date=bulletin_date,
    )


def extract_no_statsrad_articles(
    archive: _ArchiveLike,
    *,
    article_ids: Iterable[str] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    if article_ids is None:
        article_ids = [
            locator.split("/")[4]
            for locator in archive.locators("no://statsrad/article/%/record.json")
            if locator.count("/") >= 5
        ]
    else:
        article_ids = list(article_ids)
    if limit is not None:
        article_ids = list(article_ids)[:limit]
    extracted = 0
    for bulletin_id in article_ids:
        raw_bytes = archive.get(no_statsrad_article_raw_locator(bulletin_id))
        record_bytes = archive.get(no_statsrad_article_record_locator(bulletin_id))
        if raw_bytes is None or record_bytes is None:
            continue
        record = json.loads(record_bytes.decode("utf-8"))
        events = extract_no_statsrad_events(
            raw_bytes,
            bulletin_id=bulletin_id,
            bulletin_date=record.get("published_date"),
            title=record.get("title", ""),
        )
        archive.store(
            no_statsrad_article_events_locator(bulletin_id),
            _json_bytes(events),
            storage_class="json",
            metadata={"kind": "statsrad_article_events", "bulletin_id": bulletin_id, "event_count": len(events)},
        )
        extracted += 1
    return {"article_count": extracted}


def build_no_statsrad_index_report(
    *,
    data_dir: Path | None = None,
    db_path: Path | None = None,
    start_page: int = 1,
    max_pages: int | None = None,
    article_limit: int | None = None,
    max_age_hours: float = 24.0,
    skip_existing: bool = False,
) -> dict[str, Any]:
    del max_age_hours  # current live fetch path does not yet use per-request cache TTL
    return fetch_statsrad_index(
        db_path=resolve_no_source_path(db_path or data_dir),
        max_pages=max_pages,
        start_page=start_page,
        article_limit=article_limit,
        skip_existing=skip_existing,
    )


def build_no_statsrad_fetch_report(
    *,
    data_dir: Path | None = None,
    db_path: Path | None = None,
    bulletin_ids: Iterable[str] | None = None,
    max_articles: int | None = None,
    max_age_hours: float = float("inf"),
    skip_existing: bool = False,
) -> dict[str, Any]:
    del max_age_hours  # current live fetch path does not yet use per-request cache TTL
    return fetch_statsrad_articles(
        db_path=resolve_no_source_path(db_path or data_dir),
        bulletin_ids=bulletin_ids,
        limit=max_articles,
        skip_existing=skip_existing,
    )


def build_no_statsrad_extract_report(
    *,
    data_dir: Path | None = None,
    db_path: Path | None = None,
    article_ids: Iterable[str] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    return extract_statsrad_events(
        db_path=resolve_no_source_path(db_path or data_dir),
        bulletin_ids=article_ids,
        limit=limit,
    )


def iter_no_statsrad_event_artifacts(source_path: Path | None = None) -> list[dict[str, Any]]:
    archive = open_no_archive(resolve_no_source_path(source_path))
    try:
        events: list[dict[str, Any]] = []
        for locator in archive.locators("no://statsrad/article/%/events.json"):
            payload = archive.get(locator)
            if payload is None:
                continue
            bulletin_id = locator.split("/")[4] if locator.count("/") >= 5 else ""
            try:
                article_events = json.loads(payload.decode("utf-8"))
            except Exception:
                continue
            if not isinstance(article_events, list):
                continue
            for item in article_events:
                if not isinstance(item, dict):
                    continue
                event = dict(item)
                if bulletin_id and not event.get("bulletin_id"):
                    event["bulletin_id"] = bulletin_id
                event["_locator"] = locator
                events.append(event)
        return events
    finally:
        archive.close()


def build_no_statsrad_commencement_candidate_scan(
    *,
    source_id: str,
    source_title: str,
    base_ids: list[str],
    current_titles: dict[str, str],
    data_dir: Path | None = None,
    source_date: str = "",
    limit: int = 20,
    direct_only: bool = False,
) -> dict[str, Any]:
    """Return statsråd evidence candidates for a contingent amendment source.

    This stays in the evidence lane: it does not mutate replay state or
    interpret commencement overrides. The result is a pure candidate scan over
    stored statsråd event artifacts.
    """

    needles: list[tuple[str, str, int]] = []
    short_id = source_id.removeprefix("no/lovtid/")
    if short_id:
        needles.append(("source_short_id", short_id, 100))
    if source_title:
        needles.append(("source_title", source_title, 40))
    for base_id in base_ids:
        base_short = base_id.removeprefix("no/lov/")
        if base_short:
            needles.append(("base_id", base_short, 18))
        title = current_titles.get(base_id, "").strip()
        if title:
            needles.append(("base_title", title, 12))

    deduped_needles: list[tuple[str, str, int]] = []
    seen_needles: set[tuple[str, str]] = set()
    for kind, needle, score in needles:
        key = (kind, needle.lower())
        if key in seen_needles:
            continue
        seen_needles.add(key)
        deduped_needles.append((kind, needle, score))

    candidates: list[dict[str, Any]] = []
    for event in iter_no_statsrad_event_artifacts(data_dir):
        event_text = _normalize_space(str(event.get("excerpt") or event.get("raw_text") or ""))
        title = str(event.get("title") or "")
        title_text = _normalize_space(title).lower()
        matches: list[dict[str, object]] = []
        score = 0
        for kind, needle, weight in deduped_needles:
            hit = _find_literal(event_text, needle)
            if hit is None and title_text:
                hit = _find_literal(title_text, needle)
            if hit is None:
                continue
            matches.append(
                {
                    "kind": kind,
                    "needle": needle,
                    "offset": hit["offset"],
                    "excerpt": hit["excerpt"],
                    "weight": weight,
                }
            )
            score += weight
        if not matches:
            continue
        if source_date and (candidate_date := str(event.get("effective_date") or event.get("bulletin_date") or "")) and candidate_date < source_date:
            continue
        direct_match = any(str(match.get("kind", "")) in {"source_short_id", "source_title"} for match in matches)
        if direct_only and not direct_match:
            continue
        commencement_marker = str(event.get("event_kind") or "") in {"commencement", "partial_commencement"}
        if commencement_marker:
            score += 15
        candidates.append(
            {
                "candidate_source": "statsrad",
                "source_id": str(event.get("bulletin_id") or ""),
                "title": title,
                "effective_header": str(event.get("effective_date") or ""),
                "candidate_date": str(event.get("effective_date") or event.get("bulletin_date") or ""),
                "commencement_marker": commencement_marker,
                "direct_match": direct_match,
                "match_count": len(matches),
                "score": score,
                "matches": matches[:5],
                "archive": "norway.farchive",
                "member_name": str(event.get("_locator") or ""),
                "event_kind": str(event.get("event_kind") or ""),
                "bulletin_date": str(event.get("bulletin_date") or ""),
                "evidence_source_id": str(event.get("bulletin_id") or ""),
                "source_url": str(event.get("source_url") or ""),
            }
        )

    candidates.sort(
        key=lambda item: (
            not bool(item["direct_match"]),
            -int(item["score"]),
            not bool(item["commencement_marker"]),
            str(item["candidate_date"]),
            str(item["source_id"]),
        )
    )
    return {
        "candidate_source": "statsrad",
        "source_id": source_id,
        "source_title": source_title,
        "source_date": source_date,
        "direct_only": direct_only,
        "candidate_count": len(candidates),
        "candidates": candidates[:limit],
    }


def statsrad_index_page_locator(page: int) -> str:
    return no_statsrad_index_locator(page)


def statsrad_index_manifest_locator() -> str:
    return no_statsrad_manifest_locator()


def statsrad_article_raw_locator(bulletin_id: str) -> str:
    return no_statsrad_article_raw_locator(bulletin_id)


def statsrad_article_record_locator(bulletin_id: str) -> str:
    return no_statsrad_article_record_locator(bulletin_id)


def statsrad_article_events_locator(bulletin_id: str) -> str:
    return no_statsrad_article_events_locator(bulletin_id)


def bulletin_id_from_url(url: str) -> str | None:
    return no_statsrad_article_id_from_url(url)


def fetch_statsrad_url(url: str, *, timeout: float = 10.0, retries: int = 10) -> bytes:
    curl_cmd = [
        "curl",
        "--silent",
        "--show-error",
        "--location",
        "--connect-timeout",
        "5",
        "--max-time",
        str(int(timeout)),
        "--user-agent",
        "LawVM/1.0",
        "--header",
        "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "--header",
        "Accept-Language: en-US,en;q=0.7,nb-NO;q=0.6,nn-NO;q=0.5",
        "--header",
        "Cache-Control: no-cache",
        "--header",
        "Pragma: no-cache",
        "--header",
        "Connection: close",
        url,
    ]
    attempts = max(1, int(retries))
    for attempt in range(1, attempts + 1):
        try:
            completed = subprocess.run(
                curl_cmd,
                check=True,
                capture_output=True,
            )
            return completed.stdout
        except FileNotFoundError:
            break
        except subprocess.SubprocessError:
            if attempt >= attempts:
                break
            time.sleep(min(float(attempt), 5.0))

    try:
        request = Request(
            url,
            headers={
                "User-Agent": "LawVM/1.0",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.7,nb-NO;q=0.6,nn-NO;q=0.5",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "Connection": "close",
            },
        )
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except Exception:
        raise


def _extract_article_title(root: Any) -> str:
    for xpath in ("string(//main//h1[1])", "string(//article//h1[1])", "string(//h1[1])", "string(//title[1])"):
        candidate = _normalize_space(root.xpath(xpath))
        if candidate:
            return candidate
    return ""


def _extract_article_date(root: Any) -> str | None:
    for value in root.xpath("//time/@datetime"):
        date = _parse_norwegian_date(str(value))
        if date:
            return date
    for xpath in ("string(//meta[@property='article:published_time']/@content)", "string(//meta[@name='date']/@content)"):
        candidate = _normalize_space(root.xpath(xpath))
        date = _parse_norwegian_date(candidate)
        if date:
            return date
    return None


def parse_statsrad_listing_page(html_bytes: bytes, *, page: int, source_url: str) -> list[dict[str, Any]]:
    return [
        {
            "bulletin_id": article.bulletin_id,
            "url": article.url,
            "title": article.title,
            "published_date": article.published_date,
            "page": page,
        }
        for article in parse_no_statsrad_listing(html_bytes)
    ]


def _extract_title_from_excerpt(excerpt: str, fallback_title: str) -> str:
    sanction_match = re.search(
        r"Sanksjon av Stortingets vedtak(?:\s+\d{1,2}\.\s*[A-Za-zÆØÅæøå]+\s+\d{4})?(?:\s+til)?\s+(?P<title>.+?)(?:\s*\(|\.\s*Lovvedtak\b|,\s*Lovvedtak\b|\.\s*Lov nr\.|\s*Lov nr\.|$)",
        excerpt,
        re.IGNORECASE,
    )
    if sanction_match:
        candidate = _normalize_space(sanction_match.group("title")).rstrip(".,;:")
        if candidate:
            return candidate
    lov_match = re.search(
        r"\b(?P<title>Lov om .+?)(?:\s*\(|\.\s*Lovvedtak\b|,\s*Lovvedtak\b|\.\s*Lov nr\.|\s*Lov nr\.|$)",
        excerpt,
        re.IGNORECASE,
    )
    if lov_match:
        candidate = _normalize_space(lov_match.group("title")).rstrip(".,;:")
        if candidate:
            return candidate
    return fallback_title


def _extract_text_blocks(root: Any) -> list[str]:
    blocks: list[str] = []
    seen: set[str] = set()
    for node in root.xpath(
        "//main//*[self::p or self::li or self::h1 or self::h2 or self::h3]"
        " | //article//*[self::p or self::li or self::h1 or self::h2 or self::h3]"
        " | //body//*[self::p or self::li or self::h1 or self::h2 or self::h3]"
    ):
        text = _normalize_space(" ".join(node.itertext()))
        if not text or text in seen:
            continue
        blocks.append(text)
        seen.add(text)
    return blocks


def _event_confidence(
    *,
    event_kind: str,
    has_date: bool,
    has_lovvedtak: bool,
    has_law_number: bool,
    has_title_match: bool,
) -> str:
    if event_kind == "partial_commencement":
        return "partial"
    if has_date and (has_lovvedtak or has_law_number):
        return "direct_exact"
    if has_lovvedtak:
        return "lovvedtak_linked"
    if has_title_match:
        return "title_match"
    return "weak"


def extract_statsrad_events_from_article(
    html_bytes: bytes,
    *,
    bulletin_id: str,
    bulletin_url: str,
    fallback_title: str = "",
    fallback_published_date: str | None = None,
) -> list[dict[str, Any]]:
    root = html.fromstring(html_bytes)
    article_title = _extract_article_title(root) or fallback_title
    bulletin_date = _extract_article_date(root) or fallback_published_date
    blocks = _extract_text_blocks(root)
    if not blocks:
        body_text = _normalize_space(cast(Any, root).text_content())
        blocks = [body_text] if body_text else []
    events: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, raw_text in enumerate(blocks):
        raw_lower = raw_text.lower()
        excerpt = _normalize_space(" ".join(blocks[max(0, index - 1) : min(len(blocks), index + 2)]))
        has_sanction = "sanksjon av stortingets vedtak" in raw_lower
        has_partial = (
            "delt ikrafttredelse" in raw_lower
            or ("trer i kraft" in raw_lower and any(
                marker in raw_lower
                for marker in ("for øvrig", "for ovrig", "delvis", "enkelte bestemmelser", "ulike tidspunkt", "unntatt")
            ))
        )
        has_commencement = any(marker in raw_lower for marker in ("loven trer i kraft", "trer i kraft", "settes i kraft"))
        kinds: list[str] = []
        if has_sanction:
            kinds.append("sanction")
        if has_partial:
            kinds.append("partial_commencement")
        elif has_commencement:
            kinds.append("commencement")
        if not kinds:
            continue
        lovvedtak_match = _LOVVEDTAK_RE.search(excerpt)
        law_number_match = _LOV_NR_RE.search(excerpt)
        effective_dates = _extract_dates(raw_text) or _extract_dates(excerpt)
        effective_date = next(iter(effective_dates), "")
        extracted_title = _extract_title_from_excerpt(excerpt, article_title)
        has_title_match = extracted_title != article_title
        for event_kind in kinds:
            key = (event_kind, excerpt)
            if key in seen:
                continue
            events.append(
                {
                    "event_kind": event_kind,
                    "bulletin_id": bulletin_id,
                    "bulletin_date": bulletin_date,
                    "title": extracted_title,
                    "law_number": law_number_match.group(0) if law_number_match else "",
                    "lovvedtak": lovvedtak_match.group(0) if lovvedtak_match else "",
                    "effective_date": effective_date if event_kind != "sanction" else "",
                    "raw_text": raw_text,
                    "excerpt": excerpt,
                    "confidence": _event_confidence(
                        event_kind=event_kind,
                        has_date=bool(effective_date),
                        has_lovvedtak=bool(lovvedtak_match),
                        has_law_number=bool(law_number_match),
                        has_title_match=has_title_match,
                    ),
                    "partial_targets": [],
                    "source_url": bulletin_url,
                }
            )
            seen.add(key)
    return events


def fetch_statsrad_index(
    *,
    db_path: Path | str | None = None,
    max_pages: int | None = None,
    start_page: int = 1,
    article_limit: int | None = None,
    fetcher: Any | None = None,
    skip_existing: bool = False,
) -> dict[str, Any]:
    """Fetch statsråd listing pages into the Norway evidence archive.

    This lane is intentionally epistemic-only. It should improve commencement
    evidence and operator workflows, not become the hidden replay substrate.
    """
    fetcher = fetcher or fetch_statsrad_url
    archive = open_no_archive(resolve_no_source_path(Path(db_path) if db_path else None))
    discovered: dict[str, dict[str, Any]] = {}
    fetched_page_count = 0
    stored_page_count = 0
    stopped_reason = "no_list_items"
    fetch_timestamp = _now_iso()
    try:
        page = start_page
        pages_remaining = _MAX_INDEX_PAGES if max_pages is None else max_pages
        while True:
            if pages_remaining <= 0:
                stopped_reason = "safety_cap" if max_pages is None else "max_pages"
                break
            fetched_page_count += 1
            url = no_statsrad_index_url(page)
            locator = no_statsrad_index_locator(page)
            if skip_existing and archive.has(locator):
                html_bytes = archive.get(locator)
            else:
                html_bytes = fetcher(url)
                archive.store(url, html_bytes, storage_class="html")
                archive.store(locator, html_bytes, storage_class="html", metadata={"kind": "statsrad_index", "page": page, "url": url})
                stored_page_count += 1
            page_articles = parse_statsrad_listing_page(html_bytes, page=page, source_url=url)
            if not page_articles:
                stopped_reason = "no_list_items"
                break
            for article in page_articles:
                discovered.setdefault(article["bulletin_id"], article)
            if article_limit is not None and len(discovered) >= article_limit:
                stopped_reason = "article_limit"
                break
            page += 1
            pages_remaining -= 1
        manifest = {
            "source_name": STATSRAD_SOURCE_NAME,
            "fetch_timestamp": fetch_timestamp,
            "discovered_page_count": fetched_page_count,
            "stored_page_count": stored_page_count,
            "discovered_article_count": len(discovered),
            "articles": list(discovered.values()),
            "stopped_reason": stopped_reason,
            "start_page": start_page,
        }
        archive.store(no_statsrad_manifest_locator(), _json_bytes(manifest), storage_class="json", metadata={"kind": "statsrad_index_manifest", "article_count": len(discovered)})
        return manifest
    finally:
        archive.close()


def fetch_statsrad_articles(
    *,
    db_path: Path | str | None = None,
    bulletin_ids: Iterable[str] | None = None,
    limit: int | None = None,
    fetcher: Any | None = None,
    skip_existing: bool = False,
) -> dict[str, Any]:
    fetcher = fetcher or fetch_statsrad_url
    archive = open_no_archive(resolve_no_source_path(Path(db_path) if db_path else None))
    fetch_timestamp = _now_iso()
    try:
        manifest_bytes = archive.get(no_statsrad_manifest_locator())
        if manifest_bytes is None:
            raise RuntimeError("statsrad index manifest missing; run no-statsrad first")
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        selected = []
        wanted = set(bulletin_ids or [])
        for article in manifest.get("articles", []):
            if wanted and article["bulletin_id"] not in wanted:
                continue
            selected.append(article)
        if limit is not None:
            selected = selected[:limit]
        stored_raw = 0
        stored_record = 0
        stored_ids: list[str] = []
        for article in selected:
            bulletin_id = article["bulletin_id"]
            raw_locator = no_statsrad_article_raw_locator(bulletin_id)
            record_locator = no_statsrad_article_record_locator(bulletin_id)
            if skip_existing and archive.has(raw_locator) and archive.has(record_locator):
                continue
            html_bytes = fetcher(article["url"])
            archive.store(article["url"], html_bytes, storage_class="html")
            archive.store(raw_locator, html_bytes, storage_class="html", metadata={"kind": "statsrad_article_raw", "bulletin_id": bulletin_id, "url": article["url"]})
            stored_raw += 1
            record = {
                "bulletin_id": bulletin_id,
                "url": article["url"],
                "title": article.get("title", ""),
                "published_date": article.get("published_date"),
                "source_name": STATSRAD_SOURCE_NAME,
                "fetch_timestamp": fetch_timestamp,
            }
            archive.store(record_locator, _json_bytes(record), storage_class="json", metadata={"kind": "statsrad_article_record", "bulletin_id": bulletin_id, "url": article["url"]})
            stored_record += 1
            stored_ids.append(bulletin_id)
        return {
            "source_name": STATSRAD_SOURCE_NAME,
            "fetch_timestamp": fetch_timestamp,
            "selected_article_count": len(selected),
            "stored_raw_count": stored_raw,
            "stored_record_count": stored_record,
            "bulletin_ids": stored_ids,
        }
    finally:
        archive.close()


def extract_statsrad_events(
    *,
    db_path: Path | str | None = None,
    bulletin_ids: Iterable[str] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    archive = open_no_archive(resolve_no_source_path(Path(db_path) if db_path else None))
    try:
        ids = sorted(
            locator.split("/")[4]
            for locator in archive.locators("no://statsrad/article/%/raw.html")
            if locator.count("/") >= 5
        )
        wanted = set(bulletin_ids or [])
        if wanted:
            ids = [bulletin_id for bulletin_id in ids if bulletin_id in wanted]
        if limit is not None:
            ids = ids[:limit]
        reports: list[dict[str, Any]] = []
        event_count = 0
        for bulletin_id in ids:
            raw_bytes = archive.get(no_statsrad_article_raw_locator(bulletin_id))
            record_bytes = archive.get(no_statsrad_article_record_locator(bulletin_id))
            if raw_bytes is None or record_bytes is None:
                continue
            record = json.loads(record_bytes.decode("utf-8"))
            events = extract_statsrad_events_from_article(
                raw_bytes,
                bulletin_id=bulletin_id,
                bulletin_url=record.get("url", ""),
                fallback_title=record.get("title", ""),
                fallback_published_date=record.get("published_date"),
            )
            archive.store(no_statsrad_article_events_locator(bulletin_id), _json_bytes(events), storage_class="json", metadata={"kind": "statsrad_article_events", "bulletin_id": bulletin_id, "event_count": len(events)})
            reports.append({"bulletin_id": bulletin_id, "event_count": len(events)})
            event_count += len(events)
        return {
            "source_name": STATSRAD_SOURCE_NAME,
            "processed_article_count": len(reports),
            "event_count": event_count,
            "articles": reports,
        }
    finally:
        archive.close()
