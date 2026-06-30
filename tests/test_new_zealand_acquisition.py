from __future__ import annotations
from typing_extensions import override

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from lawvm.new_zealand.acquisition import (
    NZAcquisitionDiagnostic,
    NZHttpResponse,
    NZSyncOptions,
    UrllibNZTransport,
    _canonicalize_version_format_url,
    open_farchive,
    sync_nz_corpus,
)
from lawvm.new_zealand import acquisition as nz_acquisition
from lawvm.core.quirks_disposition import QuirksDisposition


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
        self.calls: list[tuple[str, str, str, float]] = []

    def get(
        self,
        url: str,
        *,
        api_key: str,
        accept: str,
        timeout_s: float,
    ) -> NZHttpResponse:
        self.calls.append((url, api_key, accept, timeout_s))
        assert "api_key=" not in url
        response = self.responses.get(url)
        if response is None:
            raise AssertionError(f"unexpected URL: {url}")
        return response


class _SequenceTransport:
    def __init__(self, responses: list[NZHttpResponse]) -> None:
        self.responses = responses
        self.calls = 0

    def get(
        self,
        url: str,
        *,
        api_key: str,
        accept: str,
        timeout_s: float,
    ) -> NZHttpResponse:
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


def test_open_farchive_defaults_to_readonly_without_creating_unused_archive(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "missing" / "unused.farchive"

    with pytest.raises(sqlite3.OperationalError):
        open_farchive(archive_path)

    assert not archive_path.exists()
    assert not archive_path.parent.exists()


def test_open_farchive_writable_mode_creates_archive(tmp_path: Path) -> None:
    archive_path = tmp_path / "created" / "nz.farchive"

    archive = open_farchive(archive_path, readonly=False)
    try:
        assert archive_path.exists()
    finally:
        archive.close()


def test_open_farchive_writable_mode_rejects_extensionless_archive(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "created" / "unused"

    with pytest.raises(ValueError, match="extensionless farchive destination"):
        open_farchive(archive_path, readonly=False)

    assert not archive_path.exists()
    assert not archive_path.parent.exists()


def test_nz_acquisition_diagnostic_jsonable_uses_standard_envelope() -> None:
    metadata: dict[str, Any] = {"work_id": "act_public_2020_1", "formats": ["XML"]}
    source_lane_selection: dict[str, Any] = {"attempted_lanes": [{"lane": "version_detail"}]}
    diagnostic = NZAcquisitionDiagnostic(
        rule_id="nz_acquire_xml_format_missing",
        phase="acquisition",
        family="source_pathology",
        reason="version detail has no XML format",
        locator="https://api.legislation.govt.nz/v0/versions/example/",
        url="https://api.legislation.govt.nz/v0/versions/example/",
        status_code=200,
        blocking=True,
        strict_disposition="block",
        quirks_disposition=QuirksDisposition.RECORD,
        metadata=metadata,
        source_lane_selection=source_lane_selection,
    )
    metadata["formats"].append("HTML")
    source_lane_selection["attempted_lanes"].append({"lane": "mutated"})

    assert diagnostic.to_jsonable() == {
        "rule_id": "nz_acquire_xml_format_missing",
        "phase": "acquisition",
        "blocking": True,
        "strict_disposition": "block",
        "quirks_disposition": "record",
        "family": "source_pathology",
        "reason": "version detail has no XML format",
        "locator": "https://api.legislation.govt.nz/v0/versions/example/",
        "url": "https://api.legislation.govt.nz/v0/versions/example/",
        "status_code": 200,
        "metadata": {"work_id": "act_public_2020_1", "formats": ("XML",)},
        "source_lane_selection": {
            "attempted_lanes": ({"lane": "version_detail"},),
        },
    }


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
    assert all(call[3] == 60.0 for call in transport.calls)


def test_nz_corpus_sync_xml_present_does_not_fetch_html(tmp_path: Path) -> None:
    version_id = "act_public_1990_109_en_2022-08-30"
    version_url = f"https://api.legislation.govt.nz/v0/versions/{version_id}/"
    xml_url = "https://www.legislation.govt.nz/act/public/1990/109/en/2022-08-30.xml"
    html_url = "https://www.legislation.govt.nz/act/public/1990/109/en/2022-08-30/"
    archive = _FakeArchive()
    transport = _FakeTransport(
        {
            version_url: _json_response(
                {
                    "version_id": version_id,
                    "work_id": "act_public_1990_109",
                    "formats": [
                        {"type": "xml", "url": xml_url},
                        {"type": "html", "url": html_url},
                    ],
                }
            ),
            xml_url: NZHttpResponse(
                status_code=200,
                body=b"<act><title>Example</title></act>",
                headers={"X-RateLimit-Remaining": "9998"},
                content_type="application/xml",
            ),
            # html_url intentionally NOT registered: a fetch would raise.
        }
    )
    options = NZSyncOptions(
        db_path=tmp_path / "nz.farchive",
        version_ids=(version_id,),
        delay=0.0,
    )

    stats = sync_nz_corpus(archive, api_key="test", options=options, transport=transport)

    assert stats.stored_xml == 1
    assert stats.stored_html == 0
    assert xml_url in archive.rows
    assert html_url not in archive.rows
    assert all(call[0] != html_url for call in transport.calls)
    assert not any(
        diag.rule_id in {"nz_html_fallback_acquired", "nz_content_absent"}
        for diag in stats.diagnostics
    )


def test_nz_corpus_sync_falls_back_to_html_when_xml_404s(tmp_path: Path) -> None:
    version_id = "act_local_1878_10_en_1878-08-29"
    version_url = f"https://api.legislation.govt.nz/v0/versions/{version_id}/"
    xml_url = "https://www.legislation.govt.nz/act/local/1878/10/en/1878-08-29.xml"
    html_url = "https://www.legislation.govt.nz/act/local/1878/10/en/latest/"
    html_body = b"<html><body><div id=\"legislation\">scan content</div></body></html>"
    archive = _FakeArchive()
    transport = _FakeTransport(
        {
            version_url: _json_response(
                {
                    "version_id": version_id,
                    "work_id": "act_local_1878_10",
                    "formats": [
                        {"type": "xml", "url": xml_url},
                        {"type": "html", "url": html_url},
                        {"type": "pdf_original_scan", "url": "https://x/scan.pdf"},
                    ],
                }
            ),
            xml_url: NZHttpResponse(
                status_code=404,
                body=b"not found",
                headers={"X-RateLimit-Remaining": "9998"},
                content_type="text/plain",
            ),
            html_url: NZHttpResponse(
                status_code=200,
                body=html_body,
                headers={"X-RateLimit-Remaining": "9997"},
                content_type="text/html",
            ),
        }
    )
    options = NZSyncOptions(
        db_path=tmp_path / "nz.farchive",
        version_ids=(version_id,),
        delay=0.0,
    )

    stats = sync_nz_corpus(archive, api_key="test", options=options, transport=transport)

    assert stats.stored_xml == 0
    assert stats.stored_html == 1
    assert xml_url not in archive.rows
    assert html_url in archive.rows
    assert archive.rows[html_url].storage_class == "html"
    assert archive.rows[html_url].series_key == f"nzleg://version/{version_id}/format/html"
    assert archive.rows[html_url].data == html_body
    rule_ids = [diag.rule_id for diag in stats.diagnostics]
    assert "nz_html_fallback_acquired" in rule_ids
    assert "nz_content_absent" not in rule_ids


def test_nz_corpus_sync_records_content_absent_when_xml_and_html_404(tmp_path: Path) -> None:
    version_id = "act_local_1841_1_en_1841-12-22"
    version_url = f"https://api.legislation.govt.nz/v0/versions/{version_id}/"
    xml_url = "https://www.legislation.govt.nz/act/local/1841/1/en/1841-12-22.xml"
    html_url = "https://www.legislation.govt.nz/act/local/1841/1/en/latest/"
    archive = _FakeArchive()
    transport = _FakeTransport(
        {
            version_url: _json_response(
                {
                    "version_id": version_id,
                    "work_id": "act_local_1841_1",
                    "formats": [
                        {"type": "xml", "url": xml_url},
                        {"type": "html", "url": html_url},
                    ],
                }
            ),
            xml_url: NZHttpResponse(
                status_code=404,
                body=b"not found",
                headers={"X-RateLimit-Remaining": "9998"},
                content_type="text/plain",
            ),
            html_url: NZHttpResponse(
                status_code=404,
                body=b"not found",
                headers={"X-RateLimit-Remaining": "9997"},
                content_type="text/plain",
            ),
        }
    )
    options = NZSyncOptions(
        db_path=tmp_path / "nz.farchive",
        version_ids=(version_id,),
        delay=0.0,
    )

    stats = sync_nz_corpus(archive, api_key="test", options=options, transport=transport)

    assert stats.stored_xml == 0
    assert stats.stored_html == 0
    rule_ids = [diag.rule_id for diag in stats.diagnostics]
    assert "nz_content_absent" in rule_ids
    assert "nz_html_fallback_acquired" not in rule_ids
    # The honest content gap is non-blocking (an absent old scan, not a fault).
    absent = next(d for d in stats.diagnostics if d.rule_id == "nz_content_absent")
    assert absent.blocking is False


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
    # No XML format URL AND no HTML format URL → honest content-absent gap after
    # the optimistic-xml-missing record.
    assert [diag.rule_id for diag in stats.diagnostics] == [
        "nz_acquire_xml_format_missing",
        "nz_content_absent",
    ]


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


def test_nz_corpus_sync_stops_metadata_tier_at_rate_limit_reserve(tmp_path: Path) -> None:
    # The metadata reserve governs the v0 API host. When the FIRST version
    # detail returns remaining<=reserve, the SECOND version detail (another
    # metadata request) is blocked and the run records a metadata-tier stop.
    # The content (www) tier is on a SEPARATE budget and is unaffected: the
    # first version's XML still lands on its own gate.
    first_id = "act_public_1990_109_en_2022-08-30"
    second_id = "act_public_1991_5_en_2020-01-01"
    first_url = f"https://api.legislation.govt.nz/v0/versions/{first_id}/"
    second_url = f"https://api.legislation.govt.nz/v0/versions/{second_id}/"
    first_xml = "https://www.legislation.govt.nz/act/public/1990/109/en/2022-08-30.xml"
    archive = _FakeArchive()
    transport = _FakeTransport(
        {
            first_url: _json_response(
                {
                    "version_id": first_id,
                    "formats": [{"format": "XML", "url": first_xml}],
                },
                remaining=100,
            ),
            first_xml: NZHttpResponse(
                status_code=200,
                body=b"<act><title>One</title></act>",
                headers={},  # www content host sends no X-RateLimit-* headers
                content_type="application/xml",
            ),
            # second_url intentionally absent: it must never be requested.
        }
    )
    options = NZSyncOptions(
        db_path=tmp_path / "nz.farchive",
        version_ids=(first_id, second_id),
        delay=0.0,
        reserve_remaining=100,
    )

    stats = sync_nz_corpus(archive, api_key="test", options=options, transport=transport)

    # Metadata tier stopped at reserve before the second detail; content tier
    # was NOT throttled by that reserve, so the first version's XML landed.
    assert stats.metadata_requests == 1
    assert stats.content_requests == 1
    assert stats.stored_xml == 1
    assert first_xml in archive.rows
    assert second_url not in [call[0] for call in transport.calls]
    assert stats.stopped_reason == "rate_limit_reserve_reached"
    rule_ids = [diag.rule_id for diag in stats.diagnostics]
    assert "nz_acquire_rate_limit_stop" in rule_ids
    stop = next(d for d in stats.diagnostics if d.rule_id == "nz_acquire_rate_limit_stop")
    assert stop.blocking is True


def test_nz_corpus_sync_content_tier_independent_of_metadata_budget(tmp_path: Path) -> None:
    # request_budget caps the METADATA tier (the documented quota). With a
    # budget of exactly 1, the single version-detail consumes it; the content
    # XML fetch still proceeds because it is on the separate content budget.
    version_id = "act_public_1990_109_en_2022-08-30"
    version_url = f"https://api.legislation.govt.nz/v0/versions/{version_id}/"
    xml_url = "https://www.legislation.govt.nz/act/public/1990/109/en/2022-08-30.xml"
    archive = _FakeArchive()
    transport = _FakeTransport(
        {
            version_url: _json_response(
                {"version_id": version_id, "formats": [{"type": "xml", "url": xml_url}]}
            ),
            xml_url: NZHttpResponse(
                status_code=200,
                body=b"<act />",
                headers={},
                content_type="application/xml",
            ),
        }
    )
    options = NZSyncOptions(
        db_path=tmp_path / "nz.farchive",
        version_ids=(version_id,),
        delay=0.0,
        request_budget=1,
    )

    stats = sync_nz_corpus(archive, api_key="test", options=options, transport=transport)

    assert stats.metadata_requests == 1
    assert stats.content_requests == 1
    assert stats.stored_xml == 1
    assert xml_url in archive.rows


def test_nz_corpus_sync_rejects_waf_challenge_content_response(tmp_path: Path) -> None:
    # A WAF challenge that slips through with a 2xx must NOT be stored as the act
    # body. It is recorded as nz_content_waf_challenge and treated as not-landed;
    # with no other manifestation that surfaces as a content gap.
    version_id = "act_local_1878_10_en_1878-08-29"
    version_url = f"https://api.legislation.govt.nz/v0/versions/{version_id}/"
    xml_url = "https://www.legislation.govt.nz/act/local/1878/10/en/1878-08-29.xml"
    html_url = "https://www.legislation.govt.nz/act/local/1878/10/en/latest/"
    archive = _FakeArchive()
    transport = _FakeTransport(
        {
            version_url: _json_response(
                {
                    "version_id": version_id,
                    "formats": [
                        {"type": "xml", "url": xml_url},
                        {"type": "html", "url": html_url},
                    ],
                }
            ),
            xml_url: NZHttpResponse(
                status_code=404,
                body=b"not found",
                headers={},
                content_type="text/plain",
            ),
            html_url: NZHttpResponse(
                status_code=202,
                body=b"",
                headers={"x-amzn-waf-action": "challenge"},
                content_type="text/html",
            ),
        }
    )
    options = NZSyncOptions(
        db_path=tmp_path / "nz.farchive",
        version_ids=(version_id,),
        delay=0.0,
    )

    stats = sync_nz_corpus(archive, api_key="test", options=options, transport=transport)

    assert stats.stored_html == 0
    assert html_url not in archive.rows
    rule_ids = [diag.rule_id for diag in stats.diagnostics]
    assert "nz_content_waf_challenge" in rule_ids
    assert "nz_html_fallback_acquired" not in rule_ids
    waf = next(d for d in stats.diagnostics if d.rule_id == "nz_content_waf_challenge")
    assert waf.blocking is True


def test_nz_corpus_sync_retries_429_before_recording_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    sleeps: list[int] = []
    monkeypatch.setattr(nz_acquisition.time, "sleep", sleeps.append)

    stats = sync_nz_corpus(archive, api_key="test", options=options, transport=transport)

    assert transport.calls == 2
    assert sleeps == [0]
    assert stats.stored_json == 1
    assert stats.diagnostics[0].rule_id == "nz_acquire_xml_format_missing"


def test_nz_corpus_sync_http_error_records_source_lane_selection(tmp_path: Path) -> None:
    version_id = "act_public_1990_109_en_2022-08-30"
    version_url = f"https://api.legislation.govt.nz/v0/versions/{version_id}/"
    archive = _FakeArchive()
    transport = _FakeTransport(
        {
            version_url: NZHttpResponse(
                status_code=500,
                body=b"server error",
                headers={"X-RateLimit-Remaining": "9999"},
                content_type="text/plain",
            ),
        }
    )
    options = NZSyncOptions(
        db_path=tmp_path / "nz.farchive",
        version_ids=(version_id,),
        delay=0.0,
    )

    stats = sync_nz_corpus(archive, api_key="test", options=options, transport=transport)

    assert [diag.rule_id for diag in stats.diagnostics] == ["nz_api_v0_version_detail_http_error"]
    detail = stats.diagnostics[0].to_jsonable()
    assert detail["blocking"] is False
    assert detail["strict_disposition"] == "record"
    source_lane = detail["source_lane_selection"]
    assert source_lane["family"] == "source_lane_selection"
    assert source_lane["selected_source_lane"] == "no_source_lane_selected_http_error"
    assert source_lane["source_lane_attempts"] == (
        {
            "lane": "nz_api_v0_version_detail",
            "lane_attempt_status": "http_500",
            "locator": version_url,
            "url": version_url,
            "content_type": "text/plain",
        },
    )


def test_nz_corpus_sync_progress_reports_to_stderr(tmp_path: Path, capsys: Any) -> None:
    version_id = "act_public_1990_109_en_2022-08-30"
    version_url = f"https://api.legislation.govt.nz/v0/versions/{version_id}/"
    archive = _FakeArchive()
    transport = _FakeTransport(
        {
            version_url: _json_response({"version_id": version_id, "formats": []}),
        }
    )
    options = NZSyncOptions(
        db_path=tmp_path / "nz.farchive",
        version_ids=(version_id,),
        delay=0.0,
        progress=True,
        progress_interval=1,
    )

    sync_nz_corpus(archive, api_key="test", options=options, transport=transport)

    err = capsys.readouterr().err
    assert "nz-sync phase=start" in err
    assert "phase=version_detail requests=1" in err
    assert "phase=done" in err
    assert "test" not in err


def test_urllib_nz_transport_uses_explicit_timeout(monkeypatch: Any) -> None:
    seen: dict[str, Any] = {}

    class FakeHeaders(dict[str, str]):
        @override
        def items(self) -> Any:
            return super().items()

    class FakeResponse:
        headers = FakeHeaders({"Content-Type": "application/json"})

        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def getcode(self) -> int:
            return 200

        def read(self) -> bytes:
            return b"{}"

    def fake_urlopen(request: Any, timeout: float) -> FakeResponse:
        seen["timeout"] = timeout
        seen["url"] = request.full_url
        seen["headers"] = dict(request.header_items())
        return FakeResponse()

    monkeypatch.setattr("lawvm.new_zealand.acquisition.urlopen", fake_urlopen)

    response = UrllibNZTransport().get(
        "https://api.legislation.govt.nz/v0/versions/example/",
        api_key="secret",
        accept="application/json",
        timeout_s=17.5,
    )

    assert response.status_code == 200
    assert seen["timeout"] == 17.5
    assert seen["url"] == "https://api.legislation.govt.nz/v0/versions/example/"
    assert seen["headers"]["X-api-key"] == "secret"


def test_urllib_nz_transport_returns_status_zero_on_timeout(monkeypatch: Any) -> None:
    def fake_urlopen(_request: Any, timeout: float) -> object:
        assert timeout == 3.0
        raise TimeoutError("timed out")

    monkeypatch.setattr("lawvm.new_zealand.acquisition.urlopen", fake_urlopen)

    response = UrllibNZTransport().get(
        "https://api.legislation.govt.nz/v0/versions/example/",
        api_key="secret",
        accept="application/json",
        timeout_s=3.0,
    )

    assert response.status_code == 0
    assert b"timed out" in response.body
