from __future__ import annotations

import datetime as dt
import hashlib
from types import SimpleNamespace
from typing import Any, cast

from lawvm.tools import uk_corpus as acquire_uk_corpus


class _FakeArchive:
    def __init__(self) -> None:
        self.store_calls: list[tuple[str, bytes, str]] = []
        self._data: dict[str, bytes] = {}

    def get(self, locator: str) -> bytes | None:
        return self._data.get(locator)

    def has(self, locator: str) -> bool:
        return locator in self._data

    def history(self, locator: str) -> list[object]:
        data = self._data.get(locator)
        if data is None:
            return []
        return [
            SimpleNamespace(
                digest=hashlib.sha256(data).hexdigest(),
                last_confirmed_at=dt.datetime.now(tz=dt.timezone.utc),
            )
        ]

    def locators(self, _pattern: str) -> list[str]:
        return sorted(self._data)

    def store(self, locator: str, data: bytes, storage_class: str = "xml") -> None:
        self.store_calls.append((locator, data, storage_class))
        self._data[locator] = data


class _FakeHTTP:
    def __init__(
        self,
        status_by_url: dict[str, int],
        *,
        data_by_url: dict[str, bytes] | None = None,
    ) -> None:
        self.calls: list[str] = []
        self._status_by_url = status_by_url
        self._data_by_url = data_by_url or {}

    def get(self, url: str) -> bytes | None:
        data, _status = self.get_with_status(url)
        return data

    def get_with_status(self, url: str) -> tuple[bytes | None, int | None]:
        self.calls.append(url)
        status = self._status_by_url[url]
        if status in (404, 410):
            return None, status
        if status >= 200 and status < 300:
            if url in self._data_by_url:
                return self._data_by_url[url], status
            return b"<xml>" + b"y" * 64 + b"</xml>", status
        return None, status


def test_do_affecting_marks_missing_laws_and_skips_retry(monkeypatch) -> None:
    aid = "ukpga/2010/1"
    archive = _FakeArchive()
    http = _FakeHTTP(
        {
            f"{acquire_uk_corpus._LEG_BASE}/{aid}/enacted/data.xml": 404,
        },
    )
    # `do_affecting` scans persisted effects in the archive; in this unit test
    # we force one known acting-on target.
    monkeypatch.setattr(
        acquire_uk_corpus, "_scan_affecting_acts", lambda archive: {aid}
    )

    # First run: hard-miss should be persisted as a permanent marker.
    assert (
        acquire_uk_corpus.do_affecting(
            cast(Any, archive), cast(Any, http), types=None
        )
        == {"fetched": 0, "failed": 0, "gone": 1}
    )
    assert archive.has(acquire_uk_corpus._missing_enacted_locator(aid))
    assert len(http.calls) == 1

    # Re-scan should be marker-aware and should not reissue the request.
    assert (
        acquire_uk_corpus.do_affecting(
            cast(Any, archive), cast(Any, http), types=None
        )
        == {"fetched": 0, "failed": 0, "gone": 0}
    )
    assert len(http.calls) == 1


def test_do_affecting_records_permanent_missing_diagnostic(monkeypatch) -> None:
    aid = "ukpga/2010/3"
    archive = _FakeArchive()
    http = _FakeHTTP(
        {
            f"{acquire_uk_corpus._LEG_BASE}/{aid}/enacted/data.xml": 404,
        },
    )
    diagnostics: list[dict[str, object]] = []
    monkeypatch.setattr(
        acquire_uk_corpus, "_scan_affecting_acts", lambda archive: {aid}
    )

    result = acquire_uk_corpus.do_affecting(
        cast(Any, archive),
        cast(Any, http),
        types=None,
        diagnostics_out=diagnostics,
    )

    assert result == {"fetched": 0, "failed": 0, "gone": 1}
    assert diagnostics == [
        {
            "rule_id": "uk_acquire_affecting_enacted_permanent_missing",
            "phase": "acquisition",
            "family": "source_pathology",
            "affecting_act_id": aid,
            "locator": acquire_uk_corpus._missing_enacted_locator(aid),
            "url": f"{acquire_uk_corpus._LEG_BASE}/{aid}/enacted/data.xml",
            "status": "permanent_missing_cached",
            "reason": "http_404",
            "blocking": False,
            "strict_disposition": "record",
            "quirks_disposition": "record",
        }
    ]


def test_do_affecting_fetches_known_urls(monkeypatch) -> None:
    aid = "ukpga/2011/2"
    archive = _FakeArchive()
    http = _FakeHTTP(
        {
            f"{acquire_uk_corpus._LEG_BASE}/{aid}/enacted/data.xml": 200,
        },
    )
    monkeypatch.setattr(
        acquire_uk_corpus, "_scan_affecting_acts", lambda archive: {aid}
    )

    result = acquire_uk_corpus.do_affecting(cast(Any, archive), cast(Any, http), types=None)

    assert result == {"fetched": 1, "failed": 0, "gone": 0}
    assert not archive.has(acquire_uk_corpus._missing_enacted_locator(aid))
    assert len(http.calls) == 1


def test_do_affecting_marks_gone_on_410(monkeypatch) -> None:
    aid = "ukpga/2010/2"
    archive = _FakeArchive()
    http = _FakeHTTP(
        {
            f"{acquire_uk_corpus._LEG_BASE}/{aid}/enacted/data.xml": 410,
        },
    )
    monkeypatch.setattr(
        acquire_uk_corpus, "_scan_affecting_acts", lambda archive: {aid}
    )

    result = acquire_uk_corpus.do_affecting(cast(Any, archive), cast(Any, http), types=None)

    assert result == {"fetched": 0, "failed": 0, "gone": 1}
    assert archive.has(acquire_uk_corpus._missing_enacted_locator(aid))
    assert len(http.calls) == 1


def test_do_affecting_records_fetch_failure_diagnostic(monkeypatch) -> None:
    aid = "ukpga/2010/4"
    archive = _FakeArchive()
    http = _FakeHTTP(
        {
            f"{acquire_uk_corpus._LEG_BASE}/{aid}/enacted/data.xml": 500,
        },
    )
    diagnostics: list[dict[str, object]] = []
    monkeypatch.setattr(
        acquire_uk_corpus, "_scan_affecting_acts", lambda archive: {aid}
    )

    result = acquire_uk_corpus.do_affecting(
        cast(Any, archive),
        cast(Any, http),
        types=None,
        diagnostics_out=diagnostics,
    )

    assert result == {"fetched": 0, "failed": 1, "gone": 0}
    assert diagnostics == [
        {
            "rule_id": "uk_acquire_affecting_enacted_fetch_failed",
            "phase": "acquisition",
            "family": "source_pathology",
            "affecting_act_id": aid,
            "locator": acquire_uk_corpus._missing_enacted_locator(aid),
            "url": f"{acquire_uk_corpus._LEG_BASE}/{aid}/enacted/data.xml",
            "status": "error",
            "reason": "http_500",
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
        }
    ]


def test_do_refresh_can_force_one_statute_current_and_effects() -> None:
    sid = "ukpga/2020/17"
    current_url = f"{acquire_uk_corpus._LEG_BASE}/{sid}/data.xml"
    feed_url = (
        f"{acquire_uk_corpus._LEG_BASE}/changes/affected/ukpga/2020/17/"
        "data.feed?results-count=50&sort=modified"
    )
    feed_page_2_url = f"{feed_url}&page=2"
    feed = (
        b'<feed xmlns:ukm="http://www.legislation.gov.uk/namespaces/legislation">'
        b"<ukm:totalPages>2</ukm:totalPages>"
        b"</feed>"
    )
    archive = _FakeArchive()
    archive.store(current_url, b"<xml>" + b"old" * 30 + b"</xml>")
    http = _FakeHTTP(
        {
            current_url: 200,
            feed_url: 200,
            feed_page_2_url: 200,
        },
        data_by_url={
            current_url: b"<xml>" + b"new" * 30 + b"</xml>",
            feed_url: feed,
            feed_page_2_url: b"<feed>page2</feed>",
        },
    )
    archive.store_calls.clear()

    result = acquire_uk_corpus.do_refresh(
        cast(Any, archive),
        cast(Any, http),
        statute_ids={sid},
        force=True,
    )

    assert result == {"current": 1, "effects": 1}
    assert http.calls == [current_url, feed_url, feed_page_2_url]
    assert archive.store_calls == [
        (current_url, b"<xml>" + b"new" * 30 + b"</xml>", "xml"),
        (feed_url, feed, "xml"),
        (feed_page_2_url, b"<feed>page2</feed>", "xml"),
    ]


def test_main_targeted_refresh_skips_corpus_enumeration(monkeypatch, tmp_path, capsys) -> None:
    calls: dict[str, object] = {}

    class FakeArchive:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def stats(self) -> object:
            return SimpleNamespace(locator_count=3, total_stored_bytes=1234)

        def close(self) -> None:
            calls["closed"] = True

    def fake_do_refresh(
        _archive: object,
        _http: object,
        *,
        statute_ids: set[str] | None,
        force: bool,
    ) -> dict[str, int]:
        calls["statute_ids"] = statute_ids
        calls["force"] = force
        return {"current": 1, "effects": 1}

    def fail_enumerate(*_args: object, **_kwargs: object) -> list[dict[str, str]]:
        raise AssertionError("targeted refresh must not enumerate corpus CSV feeds")

    monkeypatch.setattr(acquire_uk_corpus, "Farchive", FakeArchive)
    monkeypatch.setattr(acquire_uk_corpus, "_HTTP", lambda delay: object())
    monkeypatch.setattr(acquire_uk_corpus, "do_refresh", fake_do_refresh)
    monkeypatch.setattr(acquire_uk_corpus, "_enumerate_type", fail_enumerate)

    args = SimpleNamespace(
        uk_corpus_command="refresh",
        db=str(tmp_path / "uk.farchive"),
        statute=["ukpga/2020/17"],
        force_refresh=True,
        delay=0.0,
    )
    acquire_uk_corpus.main(args)

    assert calls == {
        "statute_ids": {"ukpga/2020/17"},
        "force": True,
        "closed": True,
    }
    out = capsys.readouterr().out
    assert "[refresh] mutable resources" in out
    assert "current+1  effects+1" in out


def test_decode_content_encoding_gzip_roundtrip() -> None:
    import gzip

    xml = b"<Legislation><Body/></Legislation>"
    body = gzip.compress(xml)
    assert acquire_uk_corpus._decode_content_encoding(body, "gzip") == xml


def test_decode_content_encoding_deflate_zlib_and_raw() -> None:
    import zlib

    xml = b"<Legislation/>"
    assert acquire_uk_corpus._decode_content_encoding(zlib.compress(xml), "deflate") == xml
    raw = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    raw_deflate = raw.compress(xml) + raw.flush()
    assert acquire_uk_corpus._decode_content_encoding(raw_deflate, "deflate") == xml


def test_decode_content_encoding_identity_passthrough() -> None:
    xml = b"<Legislation/>"
    assert acquire_uk_corpus._decode_content_encoding(xml, None) == xml
    assert acquire_uk_corpus._decode_content_encoding(xml, "identity") == xml
    assert acquire_uk_corpus._decode_content_encoding(xml, "") == xml


def test_is_storable_xml_accepts_xml_rejects_gzip() -> None:
    assert acquire_uk_corpus._is_storable_xml(b"<Legislation/>")
    assert acquire_uk_corpus._is_storable_xml(b"\xef\xbb\xbf  \n<Legislation/>")  # BOM + ws
    assert acquire_uk_corpus._is_storable_xml(b"<?xml version='1.0'?><x/>")
    assert not acquire_uk_corpus._is_storable_xml(b"\x1f\x8b\x08\x00rest")  # gzip magic
    assert not acquire_uk_corpus._is_storable_xml(b"\x78\x9crest")  # zlib magic
    assert not acquire_uk_corpus._is_storable_xml(b"<!doctype html>error".replace(b"<!d", b"err"))


def test_store_if_new_refuses_gzip_payload() -> None:
    ar = cast(Any, _FakeArchive())
    stored = acquire_uk_corpus._store_if_new(ar, "u", b"\x1f\x8b\x08\x00gzipbytes", "xml")
    assert stored is False
    assert ar.store_calls == []
    # a valid XML payload is stored
    assert acquire_uk_corpus._store_if_new(ar, "u", b"<Legislation/>", "xml") is True
    assert len(ar.store_calls) == 1
