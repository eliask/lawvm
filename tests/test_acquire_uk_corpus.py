from __future__ import annotations

from typing import Any, cast

from scripts import acquire_uk_corpus


class _FakeArchive:
    def __init__(self) -> None:
        self.store_calls: list[tuple[str, bytes, str]] = []
        self._data: dict[str, bytes] = {}

    def has(self, locator: str) -> bool:
        return locator in self._data

    def store(self, locator: str, data: bytes, storage_class: str = "xml") -> None:
        self.store_calls.append((locator, data, storage_class))
        self._data[locator] = data


class _FakeHTTP:
    def __init__(self, status_by_url: dict[str, int]) -> None:
        self.calls: list[str] = []
        self._status_by_url = status_by_url

    def get_with_status(self, url: str) -> tuple[bytes | None, int | None]:
        self.calls.append(url)
        status = self._status_by_url[url]
        if status in (404, 410):
            return None, status
        if status >= 200 and status < 300:
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
