from __future__ import annotations

import datetime as dt
import hashlib
import json
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

    def stats(self) -> object:
        return SimpleNamespace(locator_count=len(self._data), total_stored_bytes=0)

    def store(self, locator: str, data: bytes, storage_class: str = "xml") -> None:
        self.store_calls.append((locator, data, storage_class))
        self._data[locator] = data

    def close(self) -> None:
        return None


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
        if url in self._data_by_url:
            return self._data_by_url[url], status
        if status >= 200 and status < 300:
            return b"<xml>" + b"y" * 128 + b"</xml>", status
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
            "acquisition_status": "permanent_missing_cached",
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
            "acquisition_status": "error",
            "reason": "http_500",
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
        }
    ]


def test_do_download_fetches_multiple_choices_leaf_candidates() -> None:
    statute_id = "ukpga/1955/18"
    base = f"{acquire_uk_corpus._LEG_BASE}/{statute_id}"
    enacted_url = f"{base}/enacted/data.xml"
    current_url = f"{base}/data.xml"
    feed_url = (
        f"{acquire_uk_corpus._LEG_BASE}/changes/affected/ukpga/1955/18/"
        "data.feed?results-count=50&sort=modified"
    )
    ambiguity_blob = b"""<div id="content">
    <h1>Multiple Choices</h1>
    <p>The link that you've followed could mean either of the following:</p>
    <a href="/ukpga/Eliz2/3-4/18/enacted">Army Act 1955 (repealed)</a>
    <a href="/ukpga/Eliz2/4-5/18/enacted">Aliens' Employment Act 1955</a>
    </div>"""
    leaf_urls = [
        "https://www.legislation.gov.uk/ukpga/Eliz2/3-4/18/enacted/data.xml",
        "https://www.legislation.gov.uk/ukpga/Eliz2/3-4/18/data.xml",
        "https://www.legislation.gov.uk/ukpga/Eliz2/4-5/18/enacted/data.xml",
        "https://www.legislation.gov.uk/ukpga/Eliz2/4-5/18/data.xml",
    ]
    leaf_xml = b"<Legislation>" + (b"leaf" * 40) + b"</Legislation>"
    archive = _FakeArchive()
    http = _FakeHTTP(
        {
            enacted_url: 300,
            current_url: 300,
            feed_url: 200,
            **{url: 200 for url in leaf_urls},
        },
        data_by_url={
            enacted_url: ambiguity_blob,
            current_url: ambiguity_blob,
            feed_url: b"<feed><title>effects</title></feed>",
            **{url: leaf_xml for url in leaf_urls},
        },
    )

    result = acquire_uk_corpus.do_download(
        {"ukpga": [{"type": "ukpga", "year": "1955", "num": "18"}]},
        cast(Any, archive),
        cast(Any, http),
    )

    assert result == {
        "enacted": 0,
        "current": 0,
        "effects": 1,
        "multiple_choices": 2,
        "candidate_sources": 4,
    }
    assert not archive.has(enacted_url)
    assert not archive.has(current_url)
    for url in leaf_urls:
        assert archive.has(url)


def test_do_download_refetches_cached_multiple_choices_marker_for_candidates() -> None:
    statute_id = "ukpga/1955/18"
    enacted_url = f"{acquire_uk_corpus._LEG_BASE}/{statute_id}/enacted/data.xml"
    leaf_urls = [
        "https://www.legislation.gov.uk/ukpga/Eliz2/3-4/18/enacted/data.xml",
        "https://www.legislation.gov.uk/ukpga/Eliz2/4-5/18/enacted/data.xml",
    ]
    ambiguity_blob = b"""<div id="content">
    <h1>Multiple Choices</h1>
    <p>The link that you've followed could mean either of the following:</p>
    <a href="/ukpga/Eliz2/3-4/18/enacted">Army Act 1955 (repealed)</a>
    <a href="/ukpga/Eliz2/4-5/18/enacted">Aliens' Employment Act 1955</a>
    </div>"""
    leaf_xml = b"<Legislation>" + (b"leaf" * 40) + b"</Legislation>"
    archive = _FakeArchive()
    archive.store(enacted_url, b"HTTP 300 Multiple Choices")
    archive.store_calls.clear()
    http = _FakeHTTP(
        {
            enacted_url: 300,
            **{url: 200 for url in leaf_urls},
        },
        data_by_url={
            enacted_url: ambiguity_blob,
            **{url: leaf_xml for url in leaf_urls},
        },
    )

    result = acquire_uk_corpus.do_download(
        {"ukpga": [{"type": "ukpga", "year": "1955", "num": "18"}]},
        cast(Any, archive),
        cast(Any, http),
        enacted_only=True,
    )

    assert result == {
        "enacted": 0,
        "current": 0,
        "effects": 0,
        "multiple_choices": 1,
        "candidate_sources": 2,
    }
    assert http.calls == [enacted_url, *leaf_urls]
    assert archive.store_calls == [(url, leaf_xml, "xml") for url in leaf_urls]


def test_do_repair_multiple_choices_scans_cached_markers_for_leaf_candidates() -> None:
    statute_id = "ukpga/1955/18"
    current_url = f"{acquire_uk_corpus._LEG_BASE}/{statute_id}/data.xml"
    enacted_url = f"{acquire_uk_corpus._LEG_BASE}/{statute_id}/enacted/data.xml"
    other_url = f"{acquire_uk_corpus._LEG_BASE}/ukpga/1852/1/data.xml"
    leaf_urls = [
        "https://www.legislation.gov.uk/ukpga/Eliz2/3-4/18/enacted/data.xml",
        "https://www.legislation.gov.uk/ukpga/Eliz2/3-4/18/data.xml",
        "https://www.legislation.gov.uk/ukpga/Eliz2/4-5/18/enacted/data.xml",
        "https://www.legislation.gov.uk/ukpga/Eliz2/4-5/18/data.xml",
    ]
    ambiguity_blob = b"""<div id="content">
    <h1>Multiple Choices</h1>
    <p>The link that you've followed could mean either of the following:</p>
    <a href="/ukpga/Eliz2/3-4/18/enacted">Army Act 1955 (repealed)</a>
    <a href="/ukpga/Eliz2/4-5/18/enacted">Aliens' Employment Act 1955</a>
    </div>"""
    leaf_xml = b"<Legislation>" + (b"leaf" * 40) + b"</Legislation>"
    archive = _FakeArchive()
    archive.store(current_url, b"HTTP 300 Multiple Choices")
    archive.store(enacted_url, b"HTTP 300 Multiple Choices")
    archive.store(other_url, b"HTTP 300 Multiple Choices")
    archive.store_calls.clear()
    http = _FakeHTTP(
        {
            current_url: 300,
            enacted_url: 300,
            **{url: 200 for url in leaf_urls},
        },
        data_by_url={
            current_url: ambiguity_blob,
            enacted_url: ambiguity_blob,
            **{url: leaf_xml for url in leaf_urls},
        },
    )

    result = acquire_uk_corpus.do_repair_multiple_choices(
        cast(Any, archive),
        cast(Any, http),
        statute_ids={statute_id},
    )

    assert result == {
        "ambiguous_locators": 2,
        "ambiguous_groups": 1,
        "repaired_locators": 1,
        "candidate_source_urls": 4,
        "candidate_sources_available": 4,
        "candidate_sources": 4,
        "direct_sources": 0,
        "no_candidates": 0,
        "failed": 0,
    }
    assert http.calls == [current_url, *leaf_urls]
    assert archive.store_calls[:4] == [(url, leaf_xml, "xml") for url in leaf_urls]
    assert archive.store_calls[4][0] == acquire_uk_corpus._multiple_choice_manifest_locator(
        current_url.removesuffix("/data.xml")
    )
    assert archive.store_calls[4][2] == "json"


def test_do_repair_multiple_choices_recurses_past_self_linking_candidate() -> None:
    statute_id = "ukpga/Geo5Sess2/13/4"
    current_url = f"{acquire_uk_corpus._LEG_BASE}/{statute_id}/data.xml"
    self_enacted_url = f"{acquire_uk_corpus._LEG_BASE}/{statute_id}/enacted/data.xml"
    leaf_urls = [
        f"{acquire_uk_corpus._LEG_BASE}/ukpga/Geo5/13/4/data.xml",
        f"{acquire_uk_corpus._LEG_BASE}/ukpga/Geo5/13/4/enacted/data.xml",
    ]
    ambiguity_blob = b"""<div id="content">
    <h1>Multiple Choices</h1>
    <p>The link that you've followed could mean either of the following:</p>
    <a href="/ukpga/Geo5Sess2/13/4">Trade Facilities and Loans Guarantee Act 1922</a>
    <a href="/ukpga/Geo5/13/4">Trade Facilities and Loans Guarantee Act 1922</a>
    </div>"""
    leaf_xml = b"<Legislation>" + (b"leaf" * 40) + b"</Legislation>"
    archive = _FakeArchive()
    archive.store(current_url, b"HTTP 300 Multiple Choices")
    archive.store_calls.clear()
    http = _FakeHTTP(
        {
            current_url: 300,
            self_enacted_url: 300,
            **{url: 200 for url in leaf_urls},
        },
        data_by_url={
            current_url: ambiguity_blob,
            self_enacted_url: ambiguity_blob,
            **{url: leaf_xml for url in leaf_urls},
        },
    )

    result = acquire_uk_corpus.do_repair_multiple_choices(
        cast(Any, archive),
        cast(Any, http),
        statute_ids={statute_id},
    )

    assert result == {
        "ambiguous_locators": 1,
        "ambiguous_groups": 1,
        "repaired_locators": 1,
        "candidate_source_urls": 4,
        "candidate_sources_available": 2,
        "candidate_sources": 2,
        "direct_sources": 0,
        "no_candidates": 0,
        "failed": 0,
    }
    assert http.calls == [current_url, self_enacted_url, *leaf_urls]
    assert archive.store_calls[:2] == [(url, leaf_xml, "xml") for url in leaf_urls]
    assert archive.store_calls[2][0] == acquire_uk_corpus._multiple_choice_manifest_locator(
        current_url.removesuffix("/data.xml")
    )
    assert archive.store_calls[2][2] == "json"


def test_do_repair_multiple_choices_reports_already_available_leaf_candidates() -> None:
    statute_id = "ukpga/1955/18"
    current_url = f"{acquire_uk_corpus._LEG_BASE}/{statute_id}/data.xml"
    leaf_urls = [
        "https://www.legislation.gov.uk/ukpga/Eliz2/3-4/18/enacted/data.xml",
        "https://www.legislation.gov.uk/ukpga/Eliz2/3-4/18/data.xml",
        "https://www.legislation.gov.uk/ukpga/Eliz2/4-5/18/enacted/data.xml",
        "https://www.legislation.gov.uk/ukpga/Eliz2/4-5/18/data.xml",
    ]
    ambiguity_blob = b"""<div id="content">
    <h1>Multiple Choices</h1>
    <p>The link that you've followed could mean either of the following:</p>
    <a href="/ukpga/Eliz2/3-4/18/enacted">Army Act 1955 (repealed)</a>
    <a href="/ukpga/Eliz2/4-5/18/enacted">Aliens' Employment Act 1955</a>
    </div>"""
    leaf_xml = b"<Legislation>" + (b"cached-leaf" * 40) + b"</Legislation>"
    archive = _FakeArchive()
    archive.store(current_url, b"HTTP 300 Multiple Choices")
    for url in leaf_urls:
        archive.store(url, leaf_xml)
    archive.store_calls.clear()
    http = _FakeHTTP({current_url: 300}, data_by_url={current_url: ambiguity_blob})

    result = acquire_uk_corpus.do_repair_multiple_choices(
        cast(Any, archive),
        cast(Any, http),
        statute_ids={statute_id},
    )

    assert result == {
        "ambiguous_locators": 1,
        "ambiguous_groups": 1,
        "repaired_locators": 1,
        "candidate_source_urls": 4,
        "candidate_sources_available": 4,
        "candidate_sources": 0,
        "direct_sources": 0,
        "no_candidates": 0,
        "failed": 0,
    }
    assert http.calls == [current_url]
    manifest_locator = acquire_uk_corpus._multiple_choice_manifest_locator(
        current_url.removesuffix("/data.xml")
    )
    assert archive.store_calls == [
        (manifest_locator, archive.get(manifest_locator), "json")
    ]
    manifest = json.loads(cast(bytes, archive.get(manifest_locator)).decode("utf-8"))
    assert manifest["truth_claim"] == "candidate_leaf_witnesses_not_source_selection"
    assert manifest["source_selection_claims"] is False
    assert manifest["replay_claims"] is False
    assert manifest["candidate_source_count"] == 4
    assert manifest["candidate_sources_available"] == 4


def test_multiple_choice_candidate_source_summary_counts_available_leaves() -> None:
    statute_id = "ukpga/1955/18"
    current_url = f"{acquire_uk_corpus._LEG_BASE}/{statute_id}/data.xml"
    leaf_urls = [
        "https://www.legislation.gov.uk/ukpga/Eliz2/3-4/18/enacted/data.xml",
        "https://www.legislation.gov.uk/ukpga/Eliz2/3-4/18/data.xml",
        "https://www.legislation.gov.uk/ukpga/Eliz2/4-5/18/enacted/data.xml",
        "https://www.legislation.gov.uk/ukpga/Eliz2/4-5/18/data.xml",
    ]
    ambiguity_blob = b"""<div id="content">
    <h1>Multiple Choices</h1>
    <p>The link that you've followed could mean either of the following:</p>
    <a href="/ukpga/Eliz2/3-4/18/enacted">Army Act 1955 (repealed)</a>
    <a href="/ukpga/Eliz2/4-5/18/enacted">Aliens' Employment Act 1955</a>
    </div>"""
    leaf_xml = b"<Legislation>" + (b"cached-leaf" * 40) + b"</Legislation>"
    archive = _FakeArchive()
    archive.store(current_url, ambiguity_blob)
    for url in leaf_urls:
        archive.store(url, leaf_xml)

    assert acquire_uk_corpus._multiple_choice_candidate_source_summary(
        cast(Any, archive)
    ) == {
        "ambiguous_locators": 1,
        "ambiguous_groups": 1,
        "groups_with_candidates": 1,
        "candidate_source_urls": 4,
        "candidate_sources_available": 4,
        "groups_fully_available": 1,
        "groups_partially_available": 0,
        "groups_without_available_candidates": 0,
        "groups_without_candidate_urls": 0,
    }


def test_multiple_choice_candidate_source_summary_keeps_partial_gaps_visible() -> None:
    statute_id = "ukpga/Geo5Sess2/13/4"
    current_url = f"{acquire_uk_corpus._LEG_BASE}/{statute_id}/data.xml"
    available_leaf = f"{acquire_uk_corpus._LEG_BASE}/ukpga/Geo5/13/4/data.xml"
    ambiguity_blob = b"""<div id="content">
    <h1>Multiple Choices</h1>
    <p>The link that you've followed could mean either of the following:</p>
    <a href="/ukpga/Geo5Sess2/13/4">Trade Facilities and Loans Guarantee Act 1922</a>
    <a href="/ukpga/Geo5/13/4">Trade Facilities and Loans Guarantee Act 1922</a>
    </div>"""
    archive = _FakeArchive()
    archive.store(current_url, ambiguity_blob)
    archive.store(available_leaf, b"<Legislation>" + (b"leaf" * 40) + b"</Legislation>")

    summary = acquire_uk_corpus._multiple_choice_candidate_source_summary(
        cast(Any, archive)
    )

    assert summary["ambiguous_locators"] == 1
    assert summary["candidate_source_urls"] == 4
    assert summary["candidate_sources_available"] == 1
    assert summary["groups_fully_available"] == 0
    assert summary["groups_partially_available"] == 1
    assert summary["groups_without_available_candidates"] == 0


def test_multiple_choice_candidate_source_summary_uses_manifest_for_short_markers() -> None:
    statute_id = "ukpga/1955/18"
    current_url = f"{acquire_uk_corpus._LEG_BASE}/{statute_id}/data.xml"
    base_locator = current_url.removesuffix("/data.xml")
    leaf_urls = [
        "https://www.legislation.gov.uk/ukpga/Eliz2/3-4/18/enacted/data.xml",
        "https://www.legislation.gov.uk/ukpga/Eliz2/3-4/18/data.xml",
    ]
    leaf_xml = b"<Legislation>" + (b"cached-leaf" * 40) + b"</Legislation>"
    archive = _FakeArchive()
    archive.store(current_url, b"HTTP 300 Multiple Choices")
    for url in leaf_urls:
        archive.store(url, leaf_xml)
    acquire_uk_corpus._store_multiple_choice_manifest(
        cast(Any, archive),
        base_locator=base_locator,
        ambiguity_locators=[current_url],
        candidate_urls=leaf_urls,
    )

    summary = acquire_uk_corpus._multiple_choice_candidate_source_summary(
        cast(Any, archive)
    )

    assert summary["ambiguous_locators"] == 1
    assert summary["candidate_source_urls"] == 2
    assert summary["candidate_sources_available"] == 2
    assert summary["groups_fully_available"] == 1
    assert summary["groups_without_candidate_urls"] == 0


def test_do_repair_multiple_choices_reports_no_candidate_pages() -> None:
    url = f"{acquire_uk_corpus._LEG_BASE}/ukpga/1955/18/data.xml"
    ambiguity_blob = b"""<div id="content">
    <h1>Multiple Choices</h1>
    <p>The link that you've followed could mean either of the following:</p>
    <a href="/search">Advanced Search</a>
    </div>"""
    archive = _FakeArchive()
    archive.store(url, b"HTTP 300 Multiple Choices")
    archive.store_calls.clear()
    http = _FakeHTTP({url: 300}, data_by_url={url: ambiguity_blob})

    result = acquire_uk_corpus.do_repair_multiple_choices(
        cast(Any, archive),
        cast(Any, http),
    )

    assert result == {
        "ambiguous_locators": 1,
        "ambiguous_groups": 1,
        "repaired_locators": 0,
        "candidate_source_urls": 0,
        "candidate_sources_available": 0,
        "candidate_sources": 0,
        "direct_sources": 0,
        "no_candidates": 1,
        "failed": 0,
    }
    assert archive.store_calls == []


def test_do_repair_multiple_choices_stores_direct_xml_when_marker_goes_stale() -> None:
    url = f"{acquire_uk_corpus._LEG_BASE}/ukpga/1859/27/enacted/data.xml"
    direct_xml = b"<Legislation>" + (b"direct" * 40) + b"</Legislation>"
    archive = _FakeArchive()
    archive.store(url, b"HTTP 300 Multiple Choices")
    archive.store_calls.clear()
    http = _FakeHTTP({url: 200}, data_by_url={url: direct_xml})

    result = acquire_uk_corpus.do_repair_multiple_choices(
        cast(Any, archive),
        cast(Any, http),
    )

    assert result == {
        "ambiguous_locators": 1,
        "ambiguous_groups": 1,
        "repaired_locators": 1,
        "candidate_source_urls": 0,
        "candidate_sources_available": 0,
        "candidate_sources": 0,
        "direct_sources": 1,
        "no_candidates": 0,
        "failed": 0,
    }
    assert archive.store_calls == [(url, direct_xml, "xml")]


def test_uk_corpus_all_runs_multiple_choices_repair(monkeypatch) -> None:
    archive = _FakeArchive()
    calls: list[str] = []

    monkeypatch.setattr(acquire_uk_corpus, "_open_archive", lambda _path, **_kwargs: archive)
    monkeypatch.setattr(
        acquire_uk_corpus,
        "run_acquire",
        lambda *_args, **_kwargs: calls.append("acquire"),
    )
    monkeypatch.setattr(
        acquire_uk_corpus,
        "run_affecting",
        lambda *_args, **_kwargs: calls.append("affecting"),
    )
    monkeypatch.setattr(
        acquire_uk_corpus,
        "run_refresh",
        lambda *_args, **_kwargs: calls.append("refresh"),
    )
    monkeypatch.setattr(
        acquire_uk_corpus,
        "run_repair_multiple_choices",
        lambda *_args, **_kwargs: calls.append("repair-multiple-choices"),
    )

    acquire_uk_corpus.main(
        SimpleNamespace(
            uk_corpus_command="all",
            db=".tmp/uk.farchive",
            types=["ukpga"],
            enacted_only=False,
            delay=0,
            affecting_types=None,
            events_jsonl=None,
            statute=[],
            force_refresh=False,
            limit=0,
        )
    )

    assert calls == ["acquire", "affecting", "refresh", "repair-multiple-choices"]


def test_uk_corpus_stats_does_not_create_missing_archive(tmp_path) -> None:
    missing_archive = tmp_path / "unused"

    try:
        acquire_uk_corpus.main(
            SimpleNamespace(
                uk_corpus_command="stats",
                db=str(missing_archive),
            )
        )
    except SystemExit as exc:
        assert exc.code == f"ERROR: archive not found: {missing_archive}"
    else:
        raise AssertionError("missing stats archive should fail")

    assert not missing_archive.exists()


def test_uk_corpus_writable_archive_rejects_extensionless_creation(tmp_path) -> None:
    missing_archive = tmp_path / "unused"

    try:
        acquire_uk_corpus._open_archive(missing_archive, readonly=False)
    except ValueError as exc:
        assert "refusing to create extensionless farchive destination" in str(exc)
    else:
        raise AssertionError("extensionless writable farchive path should fail")

    assert not missing_archive.exists()


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
    assert not acquire_uk_corpus._is_storable_xml(b"<!DOCTYPE html><html></html>")
    assert not acquire_uk_corpus._is_storable_xml(b"  <html><body>error</body></html>")
    assert not acquire_uk_corpus._is_storable_xml(b"<!doctype html>error".replace(b"<!d", b"err"))


def test_store_if_new_refuses_gzip_payload() -> None:
    ar = cast(Any, _FakeArchive())
    stored = acquire_uk_corpus._store_if_new(ar, "u", b"\x1f\x8b\x08\x00gzipbytes", "xml")
    assert stored is False
    assert ar.store_calls == []
    # a valid XML payload is stored
    assert acquire_uk_corpus._store_if_new(ar, "u", b"<Legislation/>", "xml") is True
    assert len(ar.store_calls) == 1
