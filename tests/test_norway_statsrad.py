from __future__ import annotations

import json
import subprocess

from lawvm.norway.statsrad import (
    build_no_statsrad_commencement_candidate_scan,
    build_no_statsrad_index,
    extract_no_statsrad_articles,
    fetch_statsrad_url,
    fetch_no_statsrad_articles,
    no_statsrad_article_events_locator,
    no_statsrad_article_id_from_url,
    no_statsrad_article_raw_locator,
    no_statsrad_article_record_locator,
    no_statsrad_index_locator,
    no_statsrad_manifest_locator,
    parse_no_statsrad_listing,
)


_LISTING_HTML = b"""
<html>
  <body>
    <main>
      <article>
        <a href="/no/aktuelt/offisielt-fra-statsrad-27.-mai-2025/id3103197/">Offisielt fra statsr\xc3\xa5d 27. mai 2025</a>
        <time datetime="2025-05-27">27.05.2025</time>
      </article>
      <article>
        <a href="/no/aktuelt/offisielt-fra-statsrad-15.-mai-2025/id3100742/">Offisielt fra statsr\xc3\xa5d 15. mai 2025</a>
        <time datetime="2025-05-15">15.05.2025</time>
      </article>
    </main>
  </body>
</html>
"""

_ARTICLE_HTML = b"""
<html>
  <body>
    <main>
      <p>Sanksjon av Stortingets vedtak 20. mai 2025 til lov om kryptoeiendeler (kryptoeiendelsloven). Lovvedtak 56 (2024-2025). Lov nr. 20.</p>
      <p>Loven trer i kraft 1. juli 2025.</p>
      <p>Delt ikrafttredelse av loven. Loven \\xc2\\xa7 7 nr. 1 trer i kraft 1. januar 2026.</p>
    </main>
  </body>
</html>
"""


class _FakeArchive:
    def __init__(self, fetched: dict[str, bytes]) -> None:
        self.fetched = dict(fetched)
        self.stored: dict[str, bytes] = {}
        self.metadata: dict[str, dict[str, object]] = {}

    def store(self, locator: str, data: bytes, *, storage_class: str | None = None, metadata: dict[str, object] | None = None) -> str:
        self.stored[locator] = data
        if metadata is not None:
            self.metadata[locator] = dict(metadata)
        return locator

    def get(self, locator: str) -> bytes | None:
        return self.stored.get(locator)

    def has(self, locator: str, *, max_age_hours: float = 0.0) -> bool:
        return locator in self.stored

    def fetch(self, locator: str, max_age_hours: float | None = None, content_type: str | None = None) -> bytes | None:
        return self.fetched.get(locator)

    def locators(self, pattern: str = "%") -> list[str]:
        if pattern == "no://statsrad/article/%/record.json":
            return sorted(key for key in self.stored if key.endswith("/record.json"))
        return sorted(self.stored)


def test_statsrad_locator_helpers_are_stable() -> None:
    assert no_statsrad_index_locator(68) == "no://statsrad/index/page/68.html"
    assert no_statsrad_manifest_locator() == "no://statsrad/index/manifest.json"
    assert no_statsrad_article_raw_locator("id3103197") == "no://statsrad/article/id3103197/raw.html"
    assert no_statsrad_article_record_locator("id3103197") == "no://statsrad/article/id3103197/record.json"
    assert no_statsrad_article_events_locator("id3103197") == "no://statsrad/article/id3103197/events.json"


def test_statsrad_article_id_is_extracted_from_url() -> None:
    assert (
        no_statsrad_article_id_from_url(
            "https://www.regjeringen.no/no/aktuelt/offisielt-fra-statsrad-27.-mai-2025/id3103197/"
        )
        == "id3103197"
    )


def test_parse_no_statsrad_listing_discovers_bulletins() -> None:
    records = parse_no_statsrad_listing(_LISTING_HTML)

    assert [record.bulletin_id for record in records] == ["id3103197", "id3100742"]
    assert records[0].published_date == "2025-05-27"
    assert records[0].meeting_date == "2025-05-27"
    assert records[0].title == "Offisielt fra statsråd 27. mai 2025"


def test_parse_no_statsrad_listing_uses_local_time_not_broad_ancestor_date() -> None:
    html_bytes = b"""
<html>
  <body>
    <main>
      <div class="result-list">
        <article>
          <a href="/no/aktuelt/offisielt-fra-statsrad-6.-februar-2015/id2394534/">Offisielt fra statsr\xc3\xa5d 6. februar 2015</a>
        </article>
        <article>
          <a href="/no/aktuelt/offisielt-fra-statsrad-9.-januar-2015/id2358371/">Offisielt fra statsr\xc3\xa5d 9. januar 2015</a>
        </article>
      </div>
      <time datetime="2015-05-29">29.05.2015</time>
    </main>
  </body>
</html>
"""

    records = parse_no_statsrad_listing(html_bytes)

    assert len(records) == 2
    assert records[0].meeting_date == "2015-02-06"
    assert records[1].meeting_date == "2015-01-09"
    assert records[0].published_date == "2015-02-06"
    assert records[1].published_date == "2015-01-09"


def test_build_no_statsrad_index_stores_manifest_and_index_pages() -> None:
    archive = _FakeArchive(
        {
            "https://www.regjeringen.no/no/aktuelt/offisielt-fra-statsrad/offisielt-fra-statsrad1/id30297/?page=1": _LISTING_HTML,
            "https://www.regjeringen.no/no/aktuelt/offisielt-fra-statsrad/offisielt-fra-statsrad1/id30297/?page=2": _LISTING_HTML,
        }
    )

    manifest = build_no_statsrad_index(archive, start_page=1, max_pages=2)

    assert manifest["article_count"] == 2
    assert no_statsrad_index_locator(1) in archive.stored
    assert no_statsrad_manifest_locator() in archive.stored
    saved = json.loads(archive.stored[no_statsrad_manifest_locator()].decode("utf-8"))
    assert saved["article_count"] == 2


def test_build_no_statsrad_index_stops_on_empty_2xx_page() -> None:
    archive = _FakeArchive(
        {
            "https://www.regjeringen.no/no/aktuelt/offisielt-fra-statsrad/offisielt-fra-statsrad1/id30297/?page=1": _LISTING_HTML,
            "https://www.regjeringen.no/no/aktuelt/offisielt-fra-statsrad/offisielt-fra-statsrad1/id30297/?page=2": b"<html><body><main><div class='result-list'></div></main></body></html>",
        }
    )

    manifest = build_no_statsrad_index(archive, start_page=1)

    assert manifest["article_count"] == 2
    assert manifest["page_count"] == 2
    assert manifest["stopped_reason"] == "no_list_items"


def test_build_no_statsrad_index_stops_on_article_limit() -> None:
    archive = _FakeArchive(
        {
            "https://www.regjeringen.no/no/aktuelt/offisielt-fra-statsrad/offisielt-fra-statsrad1/id30297/?page=1": _LISTING_HTML,
            "https://www.regjeringen.no/no/aktuelt/offisielt-fra-statsrad/offisielt-fra-statsrad1/id30297/?page=2": _LISTING_HTML,
        }
    )

    manifest = build_no_statsrad_index(archive, start_page=1, article_limit=1)

    assert manifest["article_count"] == 2
    assert manifest["page_count"] == 1
    assert manifest["stopped_reason"] == "article_limit"


def test_fetch_no_statsrad_articles_stores_real_url_and_canonical_locators() -> None:
    archive = _FakeArchive(
        {
            "https://www.regjeringen.no/no/aktuelt/offisielt-fra-statsrad-27.-mai-2025/id3103197/": _ARTICLE_HTML,
        }
    )
    manifest = {
        "articles": [
            {
                "bulletin_id": "id3103197",
                "url": "https://www.regjeringen.no/no/aktuelt/offisielt-fra-statsrad-27.-mai-2025/id3103197/",
                "title": "Offisielt fra statsråd 27. mai 2025",
                "published_date": "2025-05-27",
            }
        ]
    }

    report = fetch_no_statsrad_articles(archive, manifest=manifest)

    assert report["stored_articles"] == 1
    assert "https://www.regjeringen.no/no/aktuelt/offisielt-fra-statsrad-27.-mai-2025/id3103197/" in archive.stored
    assert no_statsrad_article_raw_locator("id3103197") in archive.stored
    assert no_statsrad_article_record_locator("id3103197") in archive.stored


def test_extract_no_statsrad_articles_stores_event_json() -> None:
    archive = _FakeArchive({})
    archive.store(no_statsrad_article_raw_locator("id3103197"), _ARTICLE_HTML, storage_class="html")
    archive.store(
        no_statsrad_article_record_locator("id3103197"),
        json.dumps(
            {
                "bulletin_id": "id3103197",
                "url": "https://www.regjeringen.no/no/aktuelt/offisielt-fra-statsrad-27.-mai-2025/id3103197/",
                "title": "Offisielt fra statsråd 27. mai 2025",
                "published_date": "2025-05-27",
            }
        ).encode("utf-8"),
        storage_class="json",
    )

    report = extract_no_statsrad_articles(archive, article_ids=["id3103197"])

    assert report["article_count"] == 1
    events = json.loads(archive.stored[no_statsrad_article_events_locator("id3103197")].decode("utf-8"))
    assert any(event["event_kind"] == "sanction" for event in events)
    assert any(event["event_kind"] == "commencement" and event["effective_date"] == "2025-07-01" for event in events)
    assert any(event["event_kind"] == "partial_commencement" and event["effective_date"] == "2026-01-01" for event in events)


def test_fetch_statsrad_url_prefers_curl(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], check: bool, capture_output: bool) -> subprocess.CompletedProcess[bytes]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=b"<html>ok</html>", stderr=b"")

    monkeypatch.setattr("lawvm.norway.statsrad.subprocess.run", fake_run)

    data = fetch_statsrad_url("https://www.regjeringen.no/no/aktuelt/test/id1/")

    assert data == b"<html>ok</html>"
    assert calls
    assert calls[0][0] == "curl"


def test_fetch_statsrad_url_retries_curl(monkeypatch) -> None:
    attempts = {"count": 0}

    def fake_run(cmd: list[str], check: bool, capture_output: bool) -> subprocess.CompletedProcess[bytes]:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise subprocess.CalledProcessError(returncode=28, cmd=cmd, stderr=b"timeout")
        return subprocess.CompletedProcess(cmd, 0, stdout=b"<html>retry-ok</html>", stderr=b"")

    monkeypatch.setattr("lawvm.norway.statsrad.subprocess.run", fake_run)
    monkeypatch.setattr("lawvm.norway.statsrad.time.sleep", lambda seconds: None)

    data = fetch_statsrad_url("https://www.regjeringen.no/no/aktuelt/test/id1/", retries=10)

    assert data == b"<html>retry-ok</html>"
    assert attempts["count"] == 3


def test_build_no_statsrad_commencement_candidate_scan_separates_evidence(monkeypatch) -> None:
    monkeypatch.setattr(
        "lawvm.norway.statsrad.iter_no_statsrad_event_artifacts",
        lambda data_dir=None: [
            {
                "bulletin_id": "id3103197",
                "event_kind": "commencement",
                "bulletin_date": "2025-05-27",
                "effective_date": "2025-07-01",
                "title": "Lov om dokumentasjon og arkiv (arkivlova)",
                "excerpt": "Loven trer i kraft 1. juli 2025. Det gjelder lov 2025-06-20-96.",
                "raw_text": "Loven trer i kraft 1. juli 2025. Det gjelder lov 2025-06-20-96.",
                "source_url": "https://www.regjeringen.no/no/aktuelt/offisielt-fra-statsrad-27.-mai-2025/id3103197/",
                "_locator": "no://statsrad/article/id3103197/events.json",
            }
        ],
    )

    report = build_no_statsrad_commencement_candidate_scan(
        source_id="no/lovtid/2025-06-20-96",
        source_title="Lov om dokumentasjon og arkiv (arkivlova)",
        base_ids=["no/lov/2024-12-13-77"],
        current_titles={"no/lov/2024-12-13-77": "Arkivlova"},
        data_dir=None,
        source_date="2025-06-20",
        limit=5,
        direct_only=True,
    )

    assert report["candidate_count"] == 1
    assert report["candidates"][0]["candidate_source"] == "statsrad"
    assert report["candidates"][0]["commencement_marker"] is True
