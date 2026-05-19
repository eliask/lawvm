from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lawvm.new_zealand.acquisition import (
    NZHttpResponse,
    NZSyncOptions,
    _canonicalize_version_format_url,
    sync_nz_corpus,
)
from lawvm.tools.cli import _build_parser


@dataclass
class _StoredBlob:
    data: bytes
    storage_class: str | None
    metadata: dict[str, object] | None
    series_key: str | None


class _FakeArchive:
    def __init__(self) -> None:
        self.rows: dict[str, _StoredBlob] = {}

    def get(self, locator: str, *, at: object | None = None) -> bytes | None:
        row = self.rows.get(locator)
        return row.data if row else None

    def store(
        self,
        locator: str,
        data: bytes,
        *,
        observed_at: object | None = None,
        storage_class: str | None = None,
        series_key: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> str:
        self.rows[locator] = _StoredBlob(
            data=data,
            storage_class=storage_class,
            metadata=metadata,
            series_key=series_key,
        )
        return "sha256:fake"

    def close(self) -> None:
        return None


class _FakeTransport:
    def __init__(self, responses: dict[str, NZHttpResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, str]] = []

    def get(self, url: str, *, api_key: str, accept: str) -> NZHttpResponse:
        self.calls.append((url, api_key, accept))
        assert "api_key=" not in url
        response = self.responses.get(url)
        if response is None:
            raise AssertionError(f"unexpected URL: {url}")
        return response


class _SequenceTransport:
    def __init__(self, responses: list[NZHttpResponse]) -> None:
        self.responses = responses
        self.calls = 0

    def get(self, url: str, *, api_key: str, accept: str) -> NZHttpResponse:
        self.calls += 1
        if not self.responses:
            raise AssertionError("unexpected extra request")
        return self.responses.pop(0)


def _json_response(payload: dict[str, Any], remaining: int = 9999) -> NZHttpResponse:
    return NZHttpResponse(
        status_code=200,
        body=json.dumps(payload).encode(),
        headers={
            "X-RateLimit-Limit": "10000",
            "X-RateLimit-Remaining": str(remaining),
            "X-RateLimit-Reset": "2026-05-16T12:00:00Z",
        },
        content_type="application/json",
    )


def test_nz_corpus_sync_fetches_version_detail_and_xml_without_query_key(tmp_path: Path) -> None:
    version_id = "act_public_1990_109_en_2022-08-30"
    version_url = f"https://api.legislation.govt.nz/v0/versions/{version_id}/"
    xml_url = "https://www.legislation.govt.nz/act/public/1990/109/en/2022-08-30.xml"
    archive = _FakeArchive()
    transport = _FakeTransport(
        {
            version_url: _json_response(
                {
                    "version_id": version_id,
                    "work_id": "act_public_1990_109",
                    "formats": [{"format": "XML", "url": xml_url}],
                }
            ),
            xml_url: NZHttpResponse(
                status_code=200,
                body=b"<act><title>Example</title></act>",
                headers={"X-RateLimit-Remaining": "9998"},
                content_type="application/xml",
            ),
        }
    )
    options = NZSyncOptions(
        db_path=tmp_path / "nz.farchive",
        version_ids=(version_id,),
        delay=0.0,
    )

    stats = sync_nz_corpus(archive, api_key="test", options=options, transport=transport)

    assert stats.requests == 2
    assert stats.stored_json == 1
    assert stats.stored_xml == 1
    assert archive.rows[version_url].storage_class == "json"
    assert archive.rows[xml_url].storage_class == "xml"
    metadata = archive.rows[version_url].metadata or {}
    assert metadata["request_url_without_api_key"] == version_url
    assert "test" not in json.dumps(metadata)
    assert all(call[1] == "test" for call in transport.calls)


def test_nz_corpus_sync_canonicalizes_latest_xml_alias(tmp_path: Path) -> None:
    version_id = "act_public_1957_87_en_2026-04-05B"
    version_url = f"https://api.legislation.govt.nz/v0/versions/{version_id}/"
    api_xml_url = "https://www.legislation.govt.nz/act/public/1957/87/en/latest.xml"
    canonical_xml_url = "https://www.legislation.govt.nz/act/public/1957/87/en/2026-04-05B.xml"
    archive = _FakeArchive()
    transport = _FakeTransport(
        {
            version_url: _json_response(
                {
                    "version_id": version_id,
                    "work_id": "act_public_1957_87",
                    "formats": [{"type": "xml", "url": api_xml_url}],
                }
            ),
            canonical_xml_url: NZHttpResponse(
                status_code=200,
                body=b"<act />",
                headers={"X-RateLimit-Remaining": "9998"},
                content_type="application/xml",
            ),
        }
    )
    options = NZSyncOptions(
        db_path=tmp_path / "nz.farchive",
        version_ids=(version_id,),
        delay=0.0,
    )

    stats = sync_nz_corpus(archive, api_key="test", options=options, transport=transport)

    assert stats.stored_xml == 1
    assert canonical_xml_url in archive.rows
    assert api_xml_url not in archive.rows
    assert (archive.rows[canonical_xml_url].metadata or {})["api_format_url"] == api_xml_url


def test_canonicalize_version_format_url_preserves_concrete_urls() -> None:
    concrete = "https://www.legislation.govt.nz/act/public/1990/109/en/2022-08-30.xml"
    assert _canonicalize_version_format_url(concrete, "act_public_1990_109_en_2022-08-30") == concrete
    assert (
        _canonicalize_version_format_url(
            "https://www.legislation.govt.nz/act/public/1957/87/en/latest.xml",
            "act_public_1957_87_en_2026-04-05B",
        )
        == "https://www.legislation.govt.nz/act/public/1957/87/en/2026-04-05B.xml"
    )


def test_nz_corpus_sync_searches_work_versions_and_records_missing_xml(tmp_path: Path) -> None:
    work_id = "act_public_1957_087"
    version_id = "act_public_1957_087_en_2026-04-05"
    search_url = "https://api.legislation.govt.nz/v0/works/?page=1&per_page=100&search_term=summary"
    versions_url = f"https://api.legislation.govt.nz/v0/works/{work_id}/versions/?sort=desc&page=1&per_page=100"
    version_url = f"https://api.legislation.govt.nz/v0/versions/{version_id}/"
    archive = _FakeArchive()
    transport = _FakeTransport(
        {
            search_url: _json_response(
                {
                    "results": [{"work_id": work_id}],
                    "page": 1,
                    "per_page": 100,
                    "total": 1,
                }
            ),
            versions_url: _json_response({"results": [{"version_id": version_id}], "total": 1}),
            version_url: _json_response({"version_id": version_id, "work_id": work_id, "formats": []}),
        }
    )
    options = NZSyncOptions(
        db_path=tmp_path / "nz.farchive",
        search_term="summary",
        delay=0.0,
    )

    stats = sync_nz_corpus(archive, api_key="test", options=options, transport=transport)

    assert stats.requests == 3
    assert stats.works_seen == 1
    assert stats.versions_seen == 1
    assert [diag.rule_id for diag in stats.diagnostics] == ["nz_acquire_xml_format_missing"]


def test_nz_corpus_sync_limits_versions_per_work(tmp_path: Path) -> None:
    first_work = "act_public_2024_10"
    second_work = "act_public_2025_14"
    first_latest = "act_public_2024_10_en_2024-01-01"
    first_older = "act_public_2024_10_en_2023-01-01"
    second_latest = "act_public_2025_14_en_2025-01-01"
    archive = _FakeArchive()
    transport = _FakeTransport(
        {
            f"https://api.legislation.govt.nz/v0/works/{first_work}/versions/?sort=desc&page=1&per_page=100": _json_response(
                {"results": [{"version_id": first_latest}, {"version_id": first_older}], "total": 2}
            ),
            f"https://api.legislation.govt.nz/v0/works/{second_work}/versions/?sort=desc&page=1&per_page=100": _json_response(
                {"results": [{"version_id": second_latest}], "total": 1}
            ),
            f"https://api.legislation.govt.nz/v0/versions/{first_latest}/": _json_response(
                {"version_id": first_latest, "formats": []}
            ),
            f"https://api.legislation.govt.nz/v0/versions/{second_latest}/": _json_response(
                {"version_id": second_latest, "formats": []}
            ),
        }
    )
    options = NZSyncOptions(
        db_path=tmp_path / "nz.farchive",
        work_ids=(first_work, second_work),
        max_versions_per_work=1,
        delay=0.0,
    )

    stats = sync_nz_corpus(archive, api_key="test", options=options, transport=transport)

    assert stats.versions_seen == 2
    assert f"https://api.legislation.govt.nz/v0/versions/{first_latest}/" in archive.rows
    assert f"https://api.legislation.govt.nz/v0/versions/{second_latest}/" in archive.rows
    assert f"https://api.legislation.govt.nz/v0/versions/{first_older}/" not in archive.rows


def test_nz_corpus_sync_stops_at_rate_limit_reserve(tmp_path: Path) -> None:
    version_id = "act_public_1990_109_en_2022-08-30"
    version_url = f"https://api.legislation.govt.nz/v0/versions/{version_id}/"
    xml_url = "https://www.legislation.govt.nz/act/public/1990/109/en/2022-08-30.xml"
    archive = _FakeArchive()
    transport = _FakeTransport(
        {
            version_url: _json_response(
                {
                    "version_id": version_id,
                    "formats": [{"format": "XML", "url": xml_url}],
                },
                remaining=100,
            ),
        }
    )
    options = NZSyncOptions(
        db_path=tmp_path / "nz.farchive",
        version_ids=(version_id,),
        delay=0.0,
        reserve_remaining=100,
    )

    stats = sync_nz_corpus(archive, api_key="test", options=options, transport=transport)

    assert stats.requests == 1
    assert stats.stopped_reason == "rate_limit_reserve_reached"
    assert [diag.rule_id for diag in stats.diagnostics] == ["nz_acquire_rate_limit_stop"]
    assert stats.diagnostics[0].blocking is True
    assert xml_url not in archive.rows


def test_nz_corpus_sync_retries_429_before_recording_failure(tmp_path: Path) -> None:
    version_id = "act_public_1990_109_en_2022-08-30"
    version_url = f"https://api.legislation.govt.nz/v0/versions/{version_id}/"
    archive = _FakeArchive()
    transport = _SequenceTransport(
        [
            NZHttpResponse(
                status_code=429,
                body=b"rate limited",
                headers={"Retry-After": "0", "X-RateLimit-Remaining": "9999"},
                content_type="text/plain",
            ),
            _json_response({"version_id": version_id, "formats": []}),
        ]
    )
    options = NZSyncOptions(
        db_path=tmp_path / "nz.farchive",
        version_ids=(version_id,),
        delay=0.0,
        rate_limit_retry_attempts=1,
    )

    stats = sync_nz_corpus(archive, api_key="test", options=options, transport=transport)

    assert transport.calls == 2
    assert stats.stored_json == 1
    assert stats.diagnostics[0].rule_id == "nz_acquire_xml_format_missing"


def test_nz_corpus_cli_parse_defaults() -> None:
    parser = _build_parser()

    args = parser.parse_args(["nz-corpus", "sync", "--work-id", "act_public_1990_109"])

    assert args.command == "nz-corpus"
    assert args.nz_corpus_command == "sync"
    assert args.db == "data/nz_legislation.farchive"
    assert args.delay == 0.5
    assert args.reserve_remaining == 100
    assert args.version_sort == "desc"
    assert args.max_versions_per_work is None
    assert args.sleep_on_rate_limit is False
    assert args.rate_limit_retry_attempts == 3
    assert args.work_id == ["act_public_1990_109"]
